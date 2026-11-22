from django.test import SimpleTestCase

from detection_ws.services import video_rules as rules
from detection_ws.services.video_rules import FrameObservation


def det(label, confidence, x=0):
    return {
        "class_id": 0,
        "label": label,
        "confidence": confidence,
        "bbox": {"x1": x, "y1": 0, "x2": x + 10, "y2": 10},
    }


def frame(index, timestamp, *detections):
    return FrameObservation(index, timestamp, list(detections))


NO = "WITHOUT_HELMET"
YES = "WITH_HELMET"
OVER = "MORE_THAN_TWO_PERSONS"


class FilterTests(SimpleTestCase):
    def test_thresholds_are_per_class(self):
        kept = rules.filter_detections(
            [det(NO, 0.3), det(NO, 0.2), det(OVER, 0.45), det(OVER, 0.8), det("x", 0.45)]
        )
        self.assertEqual(
            [(d["label"], d["confidence"]) for d in kept], [(NO, 0.3), (OVER, 0.8)]
        )

    def test_labels_are_normalised(self):
        self.assertTrue(rules.is_violation("Without-Helmet"))
        self.assertTrue(rules.is_violation("more than two persons"))
        self.assertFalse(rules.is_violation("WITH_HELMET"))
        self.assertFalse(rules.is_violation("normal"))


class ConfirmedCountTests(SimpleTestCase):
    def test_count_needs_two_frames_or_strong_evidence(self):
        frames = [
            frame(0, 0, det(NO, 0.4)),
            frame(1, 1, det(NO, 0.4), det(NO, 0.3, 20)),
            frame(2, 2, det(NO, 0.4), det(NO, 0.3, 20), det(NO, 0.3, 40)),
        ]
        # 3 heads in one weak frame only: 2 people confirmed.
        self.assertEqual(rules.confirmed_count(frames, "without_helmet"), 2)
        frames.append(frame(3, 3, det(NO, 0.5), det(NO, 0.4, 20), det(NO, 0.3, 40)))
        self.assertEqual(rules.confirmed_count(frames, "without_helmet"), 3)

    def test_single_strong_detection_is_enough(self):
        frames = [frame(0, 0, det(OVER, 0.83))]
        self.assertEqual(rules.confirmed_count(frames, "more_than_two_persons"), 1)

    def test_single_weak_detection_is_a_hallucination(self):
        frames = [frame(0, 0, det(NO, 0.4)), frame(1, 1)]
        self.assertEqual(rules.confirmed_count(frames, "without_helmet"), 0)


class IncidentTests(SimpleTestCase):
    def test_test_mp4_three_riders_without_helmet(self):
        """Excerpt of the real model output on test.mp4 (agnostic NMS)."""
        frames = [
            frame(15, 0.5, det(NO, 0.36)),
            frame(60, 2.0, det(YES, 0.56)),  # hallucinated helmet
            frame(150, 5.0, det(OVER, 0.83), det(NO, 0.49), det(NO, 0.46, 20)),
            frame(165, 5.5, det(OVER, 0.82), det(NO, 0.45), det(NO, 0.45, 20), det(NO, 0.32, 40)),
            frame(180, 6.0, det(OVER, 0.89), det(NO, 0.41)),
            frame(210, 7.0, det(OVER, 0.83), det(NO, 0.68), det(NO, 0.51, 20), det(NO, 0.45, 40)),
            frame(540, 18.0, det("normal", 0.68), det(NO, 0.37)),
        ]
        incidents = rules.build_incidents(frames, sample_fps=2)
        # 0.5 s and 18 s are separate scenes (gaps over 3 s) with a single
        # weak bareheaded detection each: not confirmed, not reported.
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(
            incident["counts"], {"more_than_two_persons": 1, "without_helmet": 3}
        )
        self.assertEqual((incident["start"], incident["end"]), (5.0, 7.0))
        self.assertEqual(incident["key_frame"], 210)
        self.assertEqual(
            sorted(d["label"] for d in incident["detections"]), [OVER, NO, NO, NO]
        )
        self.assertEqual(rules.summarize(incidents), incident["counts"])

    def test_repeated_frames_form_one_incident(self):
        frames = [frame(i * 30, float(i), det(NO, 0.7)) for i in range(10)]
        incidents = rules.build_incidents(frames, sample_fps=1)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["frames"], 10)
        self.assertEqual(rules.summarize(incidents), {"without_helmet": 1})

    def test_separate_scenes_are_added_up(self):
        frames = [
            frame(0, 0, det(NO, 0.7)),
            frame(30, 1, det(NO, 0.7)),
            frame(600, 20, det(NO, 0.7), det(NO, 0.65, 20)),
        ]
        incidents = rules.build_incidents(frames, sample_fps=1)
        self.assertEqual([i["counts"] for i in incidents], [
            {"without_helmet": 1},
            {"without_helmet": 2},
        ])
        self.assertEqual(rules.summarize(incidents), {"without_helmet": 3})

    def test_gap_grows_with_sparse_sampling(self):
        self.assertEqual(rules.max_gap(2), 3.0)
        self.assertEqual(rules.max_gap(0.5), 5.0)

    def test_timeline_keeps_confirmed_labels_inside_incidents(self):
        frames = [
            frame(0, 0, det(NO, 0.7), det(YES, 0.5, 20)),
            frame(30, 1, det(NO, 0.7), det("USING_MOBILE", 0.45, 40)),
            frame(900, 30, det(NO, 0.3)),
        ]
        incidents = rules.build_incidents(frames, sample_fps=1)
        entries = rules.timeline(frames, incidents)
        self.assertEqual([e["frame"] for e in entries], [0, 30])
        self.assertTrue(
            all(d["label"] == NO for e in entries for d in e["detections"])
        )


class RefinementTests(SimpleTestCase):
    def test_sparse_sampling_is_refined_around_incidents(self):
        # 30 fps sampled at 0.5 fps (every 60 frames); flagged at 120 and 180.
        frames = [
            frame(0, 0),
            frame(60, 2),
            frame(120, 4, det(NO, 0.4)),
            frame(180, 6, det(NO, 0.4)),
            frame(240, 8),
        ]
        extra = rules.refinement_indices(frames, fps=30, sample_fps=0.5, budget=100)
        self.assertEqual(extra, [75, 90, 105, 135, 150, 165, 195, 210, 225])

    def test_budget_is_respected_and_spread(self):
        frames = [frame(120, 4, det(NO, 0.4))]
        extra = rules.refinement_indices(frames, fps=30, sample_fps=0.5, budget=3)
        self.assertEqual(len(extra), 3)
        self.assertLess(extra[0], 120)
        self.assertGreater(extra[-1], 120)

    def test_no_refinement_when_sampling_is_dense_enough(self):
        frames = [frame(0, 0, det(NO, 0.4))]
        self.assertEqual(rules.refinement_indices(frames, 30, 2, 100), [])
        self.assertEqual(rules.refinement_indices(frames, 30, 0.5, 0), [])
        self.assertEqual(rules.refinement_indices([frame(0, 0)], 30, 0.5, 100), [])
