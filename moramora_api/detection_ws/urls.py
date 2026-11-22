from django.urls import path

from detection_ws.views import ImageDetectionView, VideoDetectionView

urlpatterns = [
    path("image/", ImageDetectionView.as_view(), name="detection-image"),
    path("video/", VideoDetectionView.as_view(), name="detection-video"),
]
