from django.urls import path

from detection_ws.views import DetectionView

urlpatterns = [
    path("", DetectionView.as_view(), name="detection"),
]
