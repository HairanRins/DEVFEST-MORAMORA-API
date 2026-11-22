from functools import lru_cache
from threading import Lock


@lru_cache(maxsize=1)
def load_model():
    from huggingface_hub import hf_hub_download
    from ultralytics import YOLO

    weights = hf_hub_download(
        repo_id="jashwanthpeddisetty0712/violation-detection-yolov8",
        filename="best.pt",
    )
    return YOLO(weights)


_inference_lock = Lock()


class DetectorBusyError(Exception):
    pass


def detect_image(
    image, wait=None, conf=0.5, iou=0.7, agnostic_nms=False, annotate=True
):
    """Run YOLO on one BGR image.

    ``wait`` (seconds) lets callers such as video processing queue for the
    engine instead of failing immediately with ``DetectorBusyError``.
    ``agnostic_nms`` keeps a single class per object (a head cannot be both
    with and without helmet). With ``annotate=False`` no annotated image is
    drawn and ``None`` is returned in its place.
    """
    acquired = (
        _inference_lock.acquire(timeout=wait)
        if wait
        else _inference_lock.acquire(blocking=False)
    )
    if not acquired:
        raise DetectorBusyError("The detection engine is busy.")

    try:
        results = load_model().predict(
            source=image,
            conf=conf,
            iou=iou,
            agnostic_nms=agnostic_nms,
            verbose=False,
        )
        if not results:
            raise RuntimeError("The detection model returned no result.")

        result = results[0]
        boxes = result.boxes
        detections = []
        if boxes is not None:
            coordinates = boxes.xyxy.cpu().tolist()
            class_ids = boxes.cls.cpu().tolist()
            confidences = boxes.conf.cpu().tolist()
            names = result.names
            for coordinates_for_box, class_id, confidence in zip(
                coordinates,
                class_ids,
                confidences,
                strict=True,
            ):
                class_id = int(class_id)
                detections.append(
                    {
                        "class_id": class_id,
                        "label": names[class_id],
                        "confidence": float(confidence),
                        "bbox": {
                            "x1": float(coordinates_for_box[0]),
                            "y1": float(coordinates_for_box[1]),
                            "x2": float(coordinates_for_box[2]),
                            "y2": float(coordinates_for_box[3]),
                        },
                    }
                )

        return (result.plot() if annotate else None), detections
    finally:
        _inference_lock.release()
