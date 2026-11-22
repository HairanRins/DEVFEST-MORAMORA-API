import cv2
from django.conf import settings


class InvalidVideoError(ValueError):
    pass


_FALLBACK_FPS = 25.0


def check_video_signature(header):
    """Accept only MP4/MOV, AVI and WebM/MKV containers (magic bytes)."""
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return
    raise InvalidVideoError("Only MP4, MOV, AVI and WebM/MKV videos are supported.")


class VideoReader:
    """Sample frames of a video file at ``sample_fps`` within configured limits."""

    def __init__(self, path, sample_fps):
        self.capture = cv2.VideoCapture(path)
        if not self.capture.isOpened():
            self.capture.release()
            raise InvalidVideoError("The video could not be decoded.")

        fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.fps = fps if 0 < fps <= 1000 else _FALLBACK_FPS
        self.frame_count = max(int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT)), 0)
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.duration = self.frame_count / self.fps if self.frame_count else 0.0

        try:
            if self.width * self.height > settings.WEBSOCKET_MAX_IMAGE_PIXELS:
                raise InvalidVideoError("Video resolution exceeds the pixel limit.")
            if self.duration > settings.DETECTION_MAX_VIDEO_DURATION:
                raise InvalidVideoError(
                    "Video exceeds the maximum duration of "
                    f"{settings.DETECTION_MAX_VIDEO_DURATION:g} seconds."
                )
        except InvalidVideoError:
            self.capture.release()
            raise

        self.sample_fps = min(sample_fps, self.fps)
        self.step = max(1, round(self.fps / self.sample_fps))

    def metadata(self):
        return {
            "duration": round(self.duration, 3),
            "fps": round(self.fps, 3),
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
        }

    def frames(self):
        """Yield ``(frame_index, timestamp_seconds, frame)`` for sampled frames."""
        max_frames = settings.DETECTION_VIDEO_MAX_FRAMES
        max_index = int(settings.DETECTION_MAX_VIDEO_DURATION * self.fps)
        index = 0
        yielded = 0
        try:
            while yielded < max_frames and index <= max_index:
                if not self.capture.grab():
                    break
                if index % self.step == 0:
                    ok, frame = self.capture.retrieve()
                    if ok and frame is not None:
                        height, width = frame.shape[:2]
                        if width * height > settings.WEBSOCKET_MAX_IMAGE_PIXELS:
                            raise InvalidVideoError(
                                "Video resolution exceeds the pixel limit."
                            )
                        yield index, round(index / self.fps, 3), frame
                        yielded += 1
                index += 1
        finally:
            self.capture.release()
        if yielded == 0:
            raise InvalidVideoError("The video contains no readable frame.")


def read_frames(path, indices):
    """Yield ``(frame_index, frame)`` for the given frame indices, in order."""
    wanted = sorted(set(indices))
    if not wanted:
        return
    capture = cv2.VideoCapture(path)
    try:
        position = 0
        for index in wanted:
            while position < index:
                if not capture.grab():
                    return
                position += 1
            ok, frame = capture.read()
            position += 1
            if ok and frame is not None:
                yield index, frame
    finally:
        capture.release()
