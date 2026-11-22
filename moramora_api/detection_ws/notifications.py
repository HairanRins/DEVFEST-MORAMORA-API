"""Push detection events from REST uploads to the user's open WebSockets.

Each authenticated WebSocket joins the group ``detection_user_<id>``; REST
views call :func:`notify_user` so a dashboard connected on ``/ws/detection/``
receives alerts for photos and videos uploaded over HTTP.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def user_group(user_id):
    return f"detection_user_{user_id}"


def notify_user(user_id, payload):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            user_group(user_id),
            {"type": "detection.push", "payload": payload},
        )
    except Exception:
        # A notification failure must never break the HTTP response.
        logger.exception("Could not push a detection event to WebSockets.")
