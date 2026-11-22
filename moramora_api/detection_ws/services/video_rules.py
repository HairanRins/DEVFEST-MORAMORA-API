"""Rules turning raw per-frame YOLO detections into reliable video incidents.

The model (YOLOv8n) is noisy on video: helmet classes flicker between frames,
a bareheaded rider is often scored 0.3-0.5, and the same riders appear in
every sampled frame. Reporting raw detections therefore both misses people
(too strict a threshold) and repeats/hallucinates them. These rules:

1. **Per-class threshold** - each class keeps detections above its own
   confidence (heads are small and scored low; vehicle classes are reliable).
2. **One label per object** - inference uses class-agnostic NMS, so one head
   cannot be both ``with_helmet`` and ``without_helmet``.
3. **Incidents** - consecutive flagged frames (gaps up to ``max_gap``) are the
   same scene: they form one incident instead of one entry per frame.
4. **Temporal confirmation** - inside an incident, a count of N people for a
   label is kept only if N detections are seen together in at least
   ``MIN_SUPPORT_FRAMES`` frames, or in one frame with N strong detections
   (confidence >= ``STRONG_CONFIDENCE``). Isolated weak detections, the usual
   hallucinations, are dropped.
5. **Second pass** - when sampling is sparse, frames around each incident
   are analysed again at ``REFINE_FPS`` (within the frame budget).
6. **Counting people, not frames** - an incident reports how many distinct
   people/vehicles commit each infraction (the confirmed simultaneous count),
   and the video summary adds incidents up.

This module is pure Python (no Django, no model) so the rules are unit-tested.
"""

from dataclasses import dataclass, field

VIOLATION_LABELS = frozenset(
    {"without_helmet", "more_than_two_persons", "using_mobile"}
)

# Minimum confidence per class (labels normalised by ``label_key``).
MIN_CONFIDENCE = {
    "without_helmet": 0.25,
    "with_helmet": 0.25,
    "using_mobile": 0.4,
    "more_than_two_persons": 0.5,
    "normal": 0.4,
}
DEFAULT_MIN_CONFIDENCE = 0.5
# Lowest threshold above: what inference must return for the rules to work.
INFERENCE_CONFIDENCE = min(MIN_CONFIDENCE.values())
# Confidence at which a single frame is enough evidence.
STRONG_CONFIDENCE = 0.6
MIN_SUPPORT_FRAMES = 2
# Two flagged frames further apart than this belong to different incidents.
MIN_INCIDENT_GAP = 3.0
# Second pass: frames around incidents are re-analysed at least at this rate,
# so a low "précision" does not miss the moment every rider is visible.
REFINE_FPS = 2.0


def label_key(label):
    return label.strip().lower().replace("-", "_").replace(" ", "_")


def is_violation(label):
    return label_key(label) in VIOLATION_LABELS


def keep_detection(detection):
    key = label_key(detection["label"])
    return detection["confidence"] >= MIN_CONFIDENCE.get(key, DEFAULT_MIN_CONFIDENCE)


def filter_detections(detections):
    """Detections above their class threshold."""
    return [d for d in detections if keep_detection(d)]


def max_gap(sample_fps):
    """Gap (seconds) closing an incident: 2.5 sampling steps, at least 3 s."""
    return max(MIN_INCIDENT_GAP, 2.5 / sample_fps)


@dataclass
class FrameObservation:
    frame: int
    timestamp: float
    detections: list = field(default_factory=list)

    def of(self, label):
        return [d for d in self.detections if label_key(d["label"]) == label]


def confirmed_count(frames, label):
    """People (or vehicles) committing ``label``, confirmed over time.

    Largest N such that N detections of ``label`` appear together in at least
    MIN_SUPPORT_FRAMES frames, or N strong detections appear in one frame.
    """
    best = 0
    per_frame = [f.of(label) for f in frames]
    counts = sorted((len(d) for d in per_frame), reverse=True)
    if len(counts) >= MIN_SUPPORT_FRAMES:
        best = counts[MIN_SUPPORT_FRAMES - 1]
    for detections in per_frame:
        strong = sum(d["confidence"] >= STRONG_CONFIDENCE for d in detections)
        best = max(best, strong)
    return best


def group_incidents(frames, sample_fps):
    """Split frames holding a violation into incidents separated by gaps."""
    flagged = [f for f in frames if any(is_violation(d["label"]) for d in f.detections)]
    gap = max_gap(sample_fps)
    groups = []
    for frame in flagged:
        if groups and frame.timestamp - groups[-1][-1].timestamp <= gap:
            groups[-1].append(frame)
        else:
            groups.append([frame])
    return groups


def _key_frame(frames, counts):
    """Frame showing the confirmed counts best (then most confident)."""

    def score(frame):
        coverage = sum(min(len(frame.of(label)), n) for label, n in counts.items())
        confidence = sum(
            d["confidence"] for d in frame.detections if label_key(d["label"]) in counts
        )
        return coverage, confidence

    return max(frames, key=score)


def _confirmed_detections(frame, counts):
    """The frame's detections of confirmed labels, most confident first."""
    kept = []
    for label, n in counts.items():
        kept.extend(sorted(frame.of(label), key=lambda d: -d["confidence"])[:n])
    return kept


def build_incidents(frames, sample_fps):
    """Confirmed incidents of a video, in time order.

    Each incident: ``start``/``end`` (s), ``frames`` (flagged frames in it),
    ``counts`` ({label: people}), ``key_frame``/``timestamp`` and the key
    frame's confirmed ``detections``. Incidents with nothing confirmed are
    dropped.
    """
    incidents = []
    for group in group_incidents(frames, sample_fps):
        counts = {}
        for label in sorted(VIOLATION_LABELS):
            n = confirmed_count(group, label)
            if n:
                counts[label] = n
        if not counts:
            continue
        key = _key_frame(group, counts)
        incidents.append(
            {
                "start": group[0].timestamp,
                "end": group[-1].timestamp,
                "frames": len(group),
                "counts": counts,
                "key_frame": key.frame,
                "timestamp": key.timestamp,
                "detections": _confirmed_detections(key, counts),
            }
        )
    return incidents


def timeline(frames, incidents):
    """Frames inside confirmed incidents, restricted to confirmed labels."""
    entries = []
    for incident in incidents:
        labels = incident["counts"]
        for frame in frames:
            if not incident["start"] <= frame.timestamp <= incident["end"]:
                continue
            detections = [d for d in frame.detections if label_key(d["label"]) in labels]
            if detections:
                entries.append(
                    {
                        "frame": frame.frame,
                        "timestamp": frame.timestamp,
                        "detections": detections,
                        "image": None,
                    }
                )
    return entries


def summarize(incidents):
    """{label: people} over the whole video."""
    summary = {}
    for incident in incidents:
        for label, n in incident["counts"].items():
            summary[label] = summary.get(label, 0) + n
    return summary


def refinement_indices(frames, fps, sample_fps, budget):
    """Extra frame indices to analyse around incidents (second pass).

    Covers each group of flagged frames, one sampling step on both sides, at
    ``REFINE_FPS``; skips analysed frames; spread evenly within ``budget``.
    """
    step = max(1, round(fps / sample_fps))
    refine_step = max(1, round(fps / REFINE_FPS))
    if refine_step >= step or budget <= 0:
        return []
    analysed = {f.frame for f in frames}
    wanted = set()
    for group in group_incidents(frames, sample_fps):
        start = max(0, group[0].frame - step)
        end = group[-1].frame + step
        first = start + (-start) % refine_step
        wanted.update(range(first, end + 1, refine_step))
    candidates = sorted(wanted - analysed)
    if len(candidates) > budget:
        stride = len(candidates) / budget
        candidates = [candidates[int(i * stride)] for i in range(budget)]
    return candidates
