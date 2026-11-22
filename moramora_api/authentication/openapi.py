"""OpenAPI documentation helpers for the authentication API.

These serializers only describe the response envelopes for the generated
schema; they are not used to render responses.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiResponse, inline_serializer
from rest_framework import serializers

from authentication.serializers import ProfileSerializer, UserSummarySerializer


class BearerJWTScheme(OpenApiAuthenticationExtension):
    target_class = "authentication.authentication.RegisteredTokenAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT obtenu via POST /api/auth/login/ (champ data.access). "
            "Sans expiration, révoqué par /api/auth/logout/.",
        }


ErrorResponseSerializer = inline_serializer(
    name="ErrorResponse",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "errors": serializers.DictField(
            required=False,
            help_text="Erreurs de validation par champ (ex. {\"email\": [\"...\"]}).",
        ),
    },
)

MessageResponseSerializer = inline_serializer(
    name="MessageResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
    },
)

SignupResponseSerializer = inline_serializer(
    name="SignupResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(default="Signup successful"),
        "data": UserSummarySerializer(),
    },
)

LoginResponseSerializer = inline_serializer(
    name="LoginResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(default="Login successful"),
        "data": inline_serializer(
            name="LoginData",
            fields={
                "access": serializers.CharField(help_text="JWT d'accès (Bearer)."),
                "user": UserSummarySerializer(),
            },
        ),
    },
)

ProfileResponseSerializer = inline_serializer(
    name="ProfileResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(default="Profile retrieved successfully"),
        "data": ProfileSerializer(),
    },
)

HealthResponseSerializer = inline_serializer(
    name="HealthResponse",
    fields={"status": serializers.CharField(default="ok")},
)

VALIDATION_ERROR = OpenApiResponse(
    ErrorResponseSerializer, description="Données invalides."
)
UNAUTHORIZED = OpenApiResponse(
    ErrorResponseSerializer,
    description="JWT absent, invalide ou révoqué.",
)
