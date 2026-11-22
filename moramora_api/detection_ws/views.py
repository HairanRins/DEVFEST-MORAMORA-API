"""REST equivalent of the detection WebSocket.

OpenAPI cannot describe WebSocket endpoints, so this view exposes the same
YOLO pipeline over HTTP: it is testable from Swagger UI and usable by clients
that only need one-shot detection.
"""

import logging

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
from detection_ws.services.violation_service import process_image
from violation.inference import DetectorBusyError

logger = logging.getLogger(__name__)


class DetectionUploadSerializer(serializers.Serializer):
    image = serializers.FileField(help_text="Image JPEG ou PNG (5 Mio max).")


DetectionResponseSerializer = inline_serializer(
    name="DetectionResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(default="Image processed successfully"),
        "data": inline_serializer(
            name="DetectionData",
            fields={
                "image": serializers.CharField(
                    help_text="Image annotée, JPEG encodé en base64."
                ),
                "detections": serializers.ListField(
                    child=serializers.DictField(),
                    help_text="Liste de détections (voir le schéma WsDetection).",
                ),
                "alert": serializers.BooleanField(
                    help_text="true si au moins une infraction est détectée."
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
            choices=["invalid_image", "busy", "processing_error"]
        ),
        "message": serializers.CharField(),
    },
)


def _error(message, code, http_status):
    return Response(
        {"success": False, "code": code, "message": message},
        status=http_status,
    )


@extend_schema(
    tags=["Détection"],
    summary="Détecter les infractions sur une image",
    description="Équivalent REST du WebSocket `/ws/detection/` : envoie une image "
    "(multipart, champ `image`) et reçoit l'image annotée + les détections YOLOv8. "
    "Pour un flux temps réel (caméra), utiliser le WebSocket.",
    request={"multipart/form-data": DetectionUploadSerializer},
    responses={
        200: DetectionResponseSerializer,
        400: OpenApiResponse(
            DetectionErrorSerializer, description="Image absente ou invalide."
        ),
        401: UNAUTHORIZED,
        503: OpenApiResponse(
            DetectionErrorSerializer,
            description="Moteur occupé (`busy`) : réessayer.",
        ),
        500: OpenApiResponse(
            DetectionErrorSerializer, description="Erreur pendant l'inférence."
        ),
    },
)
class DetectionView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser,)

    def post(self, request):
        upload = request.FILES.get("image")
        if upload is None:
            return _error(
                "An image file is required (field 'image').",
                "invalid_image",
                status.HTTP_400_BAD_REQUEST,
            )

        try:
            if upload.size > settings.WEBSOCKET_MAX_IMAGE_SIZE:
                raise InvalidImageError("Image is empty or exceeds the size limit.")
            image = decode_image_bytes(upload.read())
        except InvalidImageError as exc:
            return _error(str(exc), "invalid_image", status.HTTP_400_BAD_REQUEST)

        try:
            annotated_image, detections = process_image(image)
            encoded_image = encode_jpeg_base64(annotated_image)
        except DetectorBusyError:
            return _error(
                "The detection engine is busy; retry this image later.",
                "busy",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Image detection failed.")
            return _error(
                "Image processing failed.",
                "processing_error",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "success": True,
                "message": "Image processed successfully",
                "data": {
                    "image": encoded_image,
                    "detections": detections,
                    "alert": bool(detections),
                },
            },
            status=status.HTTP_200_OK,
        )
