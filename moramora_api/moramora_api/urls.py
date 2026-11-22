from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from authentication.views import HealthView

urlpatterns = [
    path("api/auth/", include("authentication.urls")),
    path("api/detection/", include("detection_ws.urls")),
    path("api/health/", HealthView.as_view(), name="health"),
    # OpenAPI 3 schema + interactive documentation.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
