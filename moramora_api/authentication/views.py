from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from authentication.models import IssuedAccessToken
from authentication.openapi import (
    UNAUTHORIZED,
    VALIDATION_ERROR,
    ErrorResponseSerializer,
    HealthResponseSerializer,
    LoginResponseSerializer,
    MessageResponseSerializer,
    ProfileResponseSerializer,
    SignupResponseSerializer,
)
from authentication.serializers import (
    LoginSerializer,
    ProfileSerializer,
    SignupSerializer,
    UserSummarySerializer,
)
from authentication.tokens import UnlimitedAccessToken

User = get_user_model()


@extend_schema(
    tags=["Authentification"],
    summary="Créer un compte",
    description="Email et username uniques (insensibles à la casse). "
    "Le mot de passe doit respecter les validateurs Django.",
    auth=[],
    request=SignupSerializer,
    responses={201: SignupResponseSerializer, 400: VALIDATION_ERROR},
    examples=[
        OpenApiExample(
            "Inscription",
            request_only=True,
            value={
                "username": "john",
                "email": "john@example.com",
                "password": "A-strong-password-123!",
                "password_confirmation": "A-strong-password-123!",
            },
        )
    ],
)
class SignupView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = serializer.save()
        except IntegrityError:
            raise serializers.ValidationError(
                {"detail": "The username or email address is already in use."}
            )
        return Response(
            {
                "success": True,
                "message": "Signup successful",
                "data": UserSummarySerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Authentification"],
    summary="Se connecter et obtenir un JWT",
    description="Renvoie `data.access`, à envoyer ensuite dans "
    "`Authorization: Bearer <JWT>` (REST) ou comme sous-protocole WebSocket.",
    auth=[],
    request=LoginSerializer,
    responses={
        200: LoginResponseSerializer,
        400: VALIDATION_ERROR,
        401: OpenApiResponse(
            ErrorResponseSerializer,
            description="Identifiants invalides ou compte inactif.",
        ),
        429: OpenApiResponse(
            ErrorResponseSerializer,
            description="Trop de tentatives (LOGIN_RATE_LIMIT).",
        ),
    },
    examples=[
        OpenApiExample(
            "Connexion",
            request_only=True,
            value={"email": "john@example.com", "password": "A-strong-password-123!"},
        )
    ],
)
class LoginView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "auth_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]
        user = User.objects.filter(email__iexact=email).first()

        if user is None or not user.check_password(password) or not user.is_active:
            raise AuthenticationFailed("Invalid email or password.")

        token = UnlimitedAccessToken.for_user(user)
        try:
            with transaction.atomic():
                IssuedAccessToken.objects.create(
                    jti=token["jti"],
                    user=user,
                )
                user.last_login = timezone.now()
                user.save(update_fields=("last_login",))
        except IntegrityError as exc:
            raise AuthenticationFailed("Unable to issue an access token.") from exc

        return Response(
            {
                "success": True,
                "message": "Login successful",
                "data": {
                    "access": str(token),
                    "user": UserSummarySerializer(user).data,
                },
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Authentification"],
    summary="Se déconnecter (révoque le JWT courant)",
    request=None,
    responses={200: MessageResponseSerializer, 401: UNAUTHORIZED},
)
class LogoutView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        try:
            jti = request.auth["jti"]
        except (KeyError, TypeError) as exc:
            raise AuthenticationFailed("Token is invalid.") from exc

        revoked = IssuedAccessToken.objects.filter(
            jti=jti,
            user=request.user,
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now())
        if not revoked:
            raise AuthenticationFailed("Token is invalid or has been revoked.")

        return Response(
            {
                "success": True,
                "message": "Logout successful",
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Authentification"],
    summary="Profil de l'utilisateur connecté",
    responses={200: ProfileResponseSerializer, 401: UNAUTHORIZED},
)
class ProfileView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(
            {
                "success": True,
                "message": "Profile retrieved successfully",
                "data": ProfileSerializer(request.user).data,
            },
            status=status.HTTP_200_OK,
        )


class HealthView(APIView):
    """Liveness probe used by Render; does not touch the database."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=["Système"],
        summary="État du service",
        auth=[],
        responses={200: HealthResponseSerializer},
    )
    def get(self, request):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)
