"""REST upload endpoints for violation detection (photo and video).

OpenAPI cannot describe WebSocket endpoints, so these views expose the same
YOLO pipeline over HTTP (testable from Swagger UI). Results are also pushed to
the uploader's open ``/ws/detection/`` connections (see ``notifications``).
"""

import logging
import os
import tempfile
import uuid
from collections import Counter

from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.openapi import UNAUTHORIZED
from detection_ws.image_adapter import (
    InvalidImageError,
    decode_image_bytes,
    encode_jpeg_base64,
)
from detection_ws.notifications import notify_user
from detection_ws.services.violation_service import process_image
from detection_ws.video_adapter import (
    InvalidVideoError,
    VideoReader,
    check_video_signature,
)
from violation.inference import DetectorBusyError

logger = logging.getLogger(__name__)

# Seconds a video frame may wait for the engine (shared with WebSocket clients).
_VIDEO_FRAME_WAIT = 30


# --------------------------------------------------------------------------
# OpenAPI schemas
# --------------------------------------------------------------------------


class ImageUploadSerializer(serializers.Serializer):
    image = serializers.FileField(help_text="Photo JPEG ou PNG (5 Mio max).")


class VideoUploadSerializer(serializers.Serializer):
    video = serializers.FileField(
        help_text="Vidéo MP4, MOV, AVI ou WebM (50 Mio et 120 s max par défaut)."
    )
    sample_fps = serializers.FloatField(
        required=False,
        min_value=0.1,
        max_value=10,
        help_text="Images analysées par seconde de vidéo (défaut : 1).",
    )


_DETECTIONS_FIELD = serializers.ListField(
    child=serializers.DictField(),
    help_text="Détections (format du schéma WsDetection).",
)

ImageDetectionResponseSerializer = inline_serializer(
    name="ImageDetectionResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(default="Image processed successfully"),
        "data": inline_serializer(
            name="ImageDetectionData",
            fields={
                "upload_id": serializers.UUIDField(
                    help_text="Identifiant repris dans les messages WebSocket."
                ),
                "image": serializers.CharField(
                    help_text="Image annotée, JPEG encodé en base64."
                ),
                "detections": _DETECTIONS_FIELD,
                "alert": serializers.BooleanField(
                    help_text="true si au moins une infraction est détectée."
                ),
            },
        ),
    },
)

VideoDetectionResponseSerializer = inline_serializer(
    name="VideoDetectionResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(default="Video processed successfully"),
        "data": inline_serializer(
            name="VideoDetectionData",
            fields={
                "upload_id": serializers.UUIDField(),
                "video": inline_serializer(
                    name="VideoMetadata",
                    fields={
                        "duration": serializers.FloatField(help_text="Secondes."),
                        "fps": serializers.FloatField(),
                        "frame_count": serializers.IntegerField(),
                        "width": serializers.IntegerField(),
                        "height": serializers.IntegerField(),
                    },
                ),
                "sample_fps": serializers.FloatField(),
                "frames_analyzed": serializers.IntegerField(),
                "alert": serializers.BooleanField(),
                "summary": serializers.DictField(
                    child=serializers.IntegerField(),
                    help_text="Nombre de détections par label, ex. {\"no_helmet\": 4}.",
                ),
                "violations": serializers.ListField(
                    child=inline_serializer(
                        name="VideoViolationFrame",
                        fields={
                            "frame": serializers.IntegerField(
                                help_text="Index de l'image dans la vidéo."
                            ),
                            "timestamp": serializers.FloatField(
                                help_text="Position en secondes."
                            ),
                            "detections": _DETECTIONS_FIELD,
                            "image": serializers.CharField(
                                allow_null=True,
                                help_text="Image annotée (JPEG base64) ; null au-delà "
                                "de DETECTION_VIDEO_MAX_ANNOTATED_FRAMES images.",
                            ),
                        },
                    ),
                    help_text="Images analysées contenant au moins une infraction.",
                ),
            },
        ),
    },
)

DetectionErrorSerializer = inline_serializer(
    name="DetectionErrorResponse",
    fields={
        "success": serializers.BooleanField(default=False),
        "code": serializers.ChoiceField(
            choices=["invalid_image", "invalid_video", "busy", "processing_error"]
        ),
        "message": serializers.CharField(),
    },
)

_COMMON_ERRORS = {
    401: UNAUTHORIZED,
    503: OpenApiResponse(
        DetectionErrorSerializer, description="Moteur occupé (`busy`) : réessayer."
    ),
    500: OpenApiResponse(
        DetectionErrorSerializer, description="Erreur pendant l'inférence."
    ),
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _error(message, code, http_status):
    return Response(
        {"success": False, "code": code, "message": message},
        status=http_status,
    )


def _busy():
    return _error(
        "The detection engine is busy; retry later.",
        "busy",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _processing_error():
    logger.exception("Detection failed.")
    return _error(
        "Processing failed.",
        "processing_error",
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _alert(upload_id, source, detections, **extra):
    return {
        "type": "alert",
        "event": "DETECTION_ALERT",
        "message": "A violation has been detected",
        "source": source,
        "upload_id": upload_id,
        "data": {"detections": detections, **extra},
    }


class _video_path:
    """Context manager giving a filesystem path for an uploaded file."""

    def __init__(self, upload):
        self.upload = upload
        self.temp_path = None

    def __enter__(self):
        if hasattr(self.upload, "temporary_file_path"):
            return self.upload.temporary_file_path()
        suffix = os.path.splitext(self.upload.name or "")[1][:10]
        fd, self.temp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as destination:
            for chunk in self.upload.chunks():
                destination.write(chunk)
        return self.temp_path

    def __exit__(self, *exc_info):
        if self.temp_path:
            os.unlink(self.temp_path)
        return False


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


@extend_schema(
    tags=["Détection"],
    summary="Analyser une photo",
    description="Envoie une photo (multipart, champ `image`) et renvoie l'image "
    "annotée + les détections YOLOv8. Si une infraction est détectée, un message "
    "`alert` (`source: \"image_upload\"`) est aussi envoyé aux WebSockets "
    "`/ws/detection/` ouverts par le même utilisateur.",
    request={"multipart/form-data": ImageUploadSerializer},
    responses={
        200: ImageDetectionResponseSerializer,
        400: OpenApiResponse(
            DetectionErrorSerializer, description="Image absente ou invalide."
        ),
        **_COMMON_ERRORS,
    },
)
class ImageDetectionView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser,)

    def post(self, request):
        upload = request.FILES.get("image")
        try:
            if upload is None:
                raise InvalidImageError("An image file is required (field 'image').")
            if upload.size > settings.WEBSOCKET_MAX_IMAGE_SIZE:
                raise InvalidImageError("Image is empty or exceeds the size limit.")
            image = decode_image_bytes(upload.read())
        except InvalidImageError as exc:
            return _error(str(exc), "invalid_image", status.HTTP_400_BAD_REQUEST)

        try:
            annotated_image, detections = process_image(image)
            encoded_image = encode_jpeg_base64(annotated_image)
        except DetectorBusyError:
            return _busy()
        except Exception:
            return _processing_error()

        upload_id = str(uuid.uuid4())
        if detections:
            notify_user(
                request.user.pk, _alert(upload_id, "image_upload", detections)
            )

        return Response(
            {
                "success": True,
                "message": "Image processed successfully",
                "data": {
                    "upload_id": upload_id,
                    "image": encoded_image,
                    "detections": detections,
                    "alert": bool(detections),
                },
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Détection"],
    summary="Analyser une vidéo",
    description="Envoie une vidéo (multipart, champ `video`). Le serveur analyse "
    "`sample_fps` images par seconde (120 images max) et renvoie, pour chaque "
    "image contenant une infraction, son horodatage, ses détections et l'image "
    "annotée.\n\n"
    "**Temps réel via WebSocket** : pendant le traitement, chaque infraction est "
    "envoyée aux WebSockets `/ws/detection/` de l'utilisateur (`alert`, "
    "`source: \"video_upload\"`, avec `frame` et `timestamp`), puis un message "
    "`video_completed` signale la fin. Le traitement est synchrone : la requête "
    "HTTP dure le temps de l'analyse.",
    request={"multipart/form-data": VideoUploadSerializer},
    responses={
        200: VideoDetectionResponseSerializer,
        400: OpenApiResponse(
            DetectionErrorSerializer, description="Vidéo absente ou invalide."
        ),
        **_COMMON_ERRORS,
    },
)
class VideoDetectionView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser,)

    def post(self, request):
        params = VideoUploadSerializer(data=request.data)
        if not params.is_valid():
            if "video" in params.errors:
                return _error(
                    "A video file is required (field 'video').",
                    "invalid_video",
                    status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {
                    "success": False,
                    "message": "Request validation failed.",
                    "errors": params.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        upload = params.validated_data["video"]
        sample_fps = params.validated_data.get(
            "sample_fps", settings.DETECTION_VIDEO_SAMPLE_FPS
        )
        if not upload.size or upload.size > settings.DETECTION_MAX_VIDEO_SIZE:
            return _error(
                "Video is empty or exceeds the size limit.",
                "invalid_video",
                status.HTTP_400_BAD_REQUEST,
            )

        try:
            with _video_path(upload) as path:
                return self._process(request.user.pk, path, sample_fps)
        except InvalidVideoError as exc:
            return _error(str(exc), "invalid_video", status.HTTP_400_BAD_REQUEST)

    def _process(self, user_id, path, sample_fps):
        with open(path, "rb") as video_file:
            check_video_signature(video_file.read(16))
        reader = VideoReader(path, sample_fps)

        upload_id = str(uuid.uuid4())
        violations = []
        summary = Counter()
        frames_analyzed = 0
        try:
            for frame_index, timestamp, frame in reader.frames():
                annotated, detections = process_image(frame, wait=_VIDEO_FRAME_WAIT)
                frames_analyzed += 1
                if not detections:
                    continue
                summary.update(d["label"] for d in detections)
                image = (
                    encode_jpeg_base64(annotated)
                    if len(violations) < settings.DETECTION_VIDEO_MAX_ANNOTATED_FRAMES
                    else None
                )
                violations.append(
                    {
                        "frame": frame_index,
                        "timestamp": timestamp,
                        "detections": detections,
                        "image": image,
                    }
                )
                notify_user(
                    user_id,
                    _alert(
                        upload_id,
                        "video_upload",
                        detections,
                        frame=frame_index,
                        timestamp=timestamp,
                    ),
                )
        except InvalidVideoError:
            raise
        except DetectorBusyError:
            return _busy()
        except Exception:
            return _processing_error()

        data = {
            "upload_id": upload_id,
            "video": reader.metadata(),
            "sample_fps": reader.sample_fps,
            "frames_analyzed": frames_analyzed,
            "alert": bool(violations),
            "summary": dict(summary),
            "violations": violations,
        }
        notify_user(
            user_id,
            {
                "type": "video_completed",
                "upload_id": upload_id,
                "data": {
                    key: data[key]
                    for key in ("video", "frames_analyzed", "alert", "summary")
                },
            },
        )
        return Response(
            {
                "success": True,
                "message": "Video processed successfully",
                "data": data,
            },
            status=status.HTTP_200_OK,
        )
