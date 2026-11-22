from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "authentication"

    def ready(self):
        # Registers the OpenAPI bearer scheme for RegisteredTokenAuthentication.
        from authentication import openapi  # noqa: F401
