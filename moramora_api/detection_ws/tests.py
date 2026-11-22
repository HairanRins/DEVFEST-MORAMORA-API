import asyncio
import base64
import os
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import cv2
import numpy as np
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.conf import settings

from authentication.models import IssuedAccessToken
from authentication.tokens import UnlimitedAccessToken
from detection_ws.authentication import JWTAuthMiddleware
from moramora_api.asgi import application
from violation import inference

User = get_user_model()


class FakeTensor:
    def __init__(self, value):
        self.value = value

    def cpu(self):
        return self

    def tolist(self):
        return self.value


class FakeBoxes:
    xyxy = FakeTensor([[1.0, 2.0, 8.0, 9.0]])
    cls = FakeTensor([0])
    conf = FakeTensor([0.95])


class FakeResult:
    boxes = FakeBoxes()
    names = {0: "violation"}

    def __init__(self, image):
        self.image = image

    def plot(self):
        return np.full_like(self.image, (0, 255, 0))


class FakeModel:
    def predict(self, source, conf, verbose):
        return [FakeResult(source)]


@override_settings(
    CHANNEL_LAYERS={
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }
)
class DetectionWebSocketTests(TransactionTestCase):
    allowed_origin = b"http://localhost:3000"

    def setUp(self):
        self.user = User.objects.create_user(
            username="websocket-user",
            email="websocket@example.com",
            password="Secure-password-951!",
        )
        self.token = UnlimitedAccessToken.for_user(self.user)
        IssuedAccessToken.objects.create(jti=self.token["jti"], user=self.user)
        self.image_bytes = self.make_png()

    @staticmethod
    def make_png():
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise RuntimeError("Could not encode the websocket test image.")
        return encoded.tobytes()

    def communicator(self, token=None, origin=allowed_origin, headers=None):
        protocols = ["bearer", str(token)] if token else []
        communicator = WebsocketCommunicator(
            application,
            "/ws/detection/",
            headers=[(b"origin", origin)] if headers is None else headers,
            subprotocols=protocols,
        )
        return communicator

    def test_valid_jwt_connects_and_is_associated_with_user(self):
        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.disconnect()

        async_to_sync(run)()

        authenticated_scope = {}

        async def capture_scope(scope, receive, send):
            authenticated_scope.update(scope)

        async def run_middleware():
            await JWTAuthMiddleware(capture_scope)(
                {
                    "type": "websocket",
                    "headers": [],
                    "subprotocols": ["bearer", str(self.token)],
                },
                None,
                None,
            )

        async_to_sync(run_middleware)()
        self.assertEqual(authenticated_scope["user"].id, self.user.id)

    def test_authorization_header_authenticates_non_browser_clients(self):
        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/detection/",
                headers=[
                    (b"origin", self.allowed_origin),
                    (b"authorization", f"Bearer {self.token}".encode("ascii")),
                ],
            )
            connected, _ = await communicator.connect()
            await communicator.disconnect()
            return connected

        self.assertTrue(async_to_sync(run)())

    def test_missing_and_revoked_tokens_are_rejected(self):
        IssuedAccessToken.objects.filter(jti=self.token["jti"]).update(
            revoked_at=self.user.date_joined
        )
        token_parts = str(self.token).split(".")
        invalid_signature = ".".join((*token_parts[:2], "invalid-signature"))

        async def run():
            anonymous = self.communicator()
            anonymous_connected, anonymous_close = await anonymous.connect()
            revoked = self.communicator(self.token)
            revoked_connected, revoked_close = await revoked.connect()
            invalid = self.communicator(invalid_signature)
            invalid_connected, invalid_close = await invalid.connect()
            return (
                anonymous_connected,
                anonymous_close,
                revoked_connected,
                revoked_close,
                invalid_connected,
                invalid_close,
            )

        results = async_to_sync(run)()
        self.assertEqual(results, (False, 4401, False, 4401, False, 4401))

    def test_inactive_accounts_are_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))

        async def run():
            communicator = self.communicator(self.token)
            return await communicator.connect()

        connected, close_code = async_to_sync(run)()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4401)

    def test_missing_or_untrusted_origin_is_rejected(self):
        async def run():
            no_origin = self.communicator(headers=[])
            no_origin_result = await no_origin.connect()
            untrusted = self.communicator(
                self.token,
                origin=b"https://untrusted.example",
            )
            untrusted_result = await untrusted.connect()
            return no_origin_result, untrusted_result

        results = async_to_sync(run)()
        self.assertFalse(results[0][0])
        self.assertFalse(results[1][0])

    def test_binary_image_uses_prototype_and_returns_annotation_and_alert(self):
        fake_model = FakeModel()

        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=self.image_bytes)
            result = await communicator.receive_json_from()
            alert = await communicator.receive_json_from()
            await communicator.disconnect()
            return result, alert

        with patch.object(inference, "load_model", return_value=fake_model):
            result, alert = async_to_sync(run)()

        self.assertEqual(result["type"], "detection_result")
        self.assertEqual(result["data"]["detections"][0]["label"], "violation")
        self.assertEqual(result["data"]["detections"][0]["bbox"]["x2"], 8.0)
        annotated = cv2.imdecode(
            np.frombuffer(
                base64.b64decode(result["data"]["image"]),
                dtype=np.uint8,
            ),
            cv2.IMREAD_COLOR,
        )
        self.assertGreater(float(annotated[:, :, 1].mean()), 245.0)
        self.assertLess(float(annotated[:, :, 0].mean()), 5.0)
        self.assertLess(float(annotated[:, :, 2].mean()), 5.0)
        self.assertEqual(alert["type"], "alert")
        self.assertEqual(alert["event"], "DETECTION_ALERT")

    @skipUnless(
        os.getenv("RUN_MODEL_INTEGRATION_TESTS") == "1",
        "Set RUN_MODEL_INTEGRATION_TESTS=1 to run the real YOLO integration.",
    )
    def test_real_model_through_websocket(self):
        image_path = (
            Path(settings.BASE_DIR.parent)
            / "violation"
            / "Image0775_jpg.rf.zixLWQaadRkNFH9RAH1P.jpg"
        )
        image = cv2.imread(str(image_path))
        self.assertIsNotNone(image)
        success, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(success)

        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=encoded.tobytes())
            result = await communicator.receive_json_from(timeout=120)
            alert = await communicator.receive_json_from(timeout=120)
            await communicator.disconnect()
            return result, alert

        result, alert = async_to_sync(run)()
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["detections"])
        self.assertEqual(alert["event"], "DETECTION_ALERT")

    def test_jpeg_binary_image_is_accepted(self):
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        success, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(success)

        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=encoded.tobytes())
            result = await communicator.receive_json_from()
            await communicator.receive_json_from()
            await communicator.disconnect()
            return result

        with patch.object(inference, "load_model", return_value=FakeModel()):
            result = async_to_sync(run)()
        self.assertEqual(result["type"], "detection_result")

    def test_base64_image_is_accepted(self):
        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to(
                {
                    "type": "image",
                    "image": base64.b64encode(self.image_bytes).decode("ascii"),
                }
            )
            result = await communicator.receive_json_from()
            alert = await communicator.receive_json_from()
            await communicator.disconnect()
            return result, alert

        with patch.object(inference, "load_model", return_value=FakeModel()):
            result, alert = async_to_sync(run)()
        self.assertEqual(result["type"], "detection_result")
        self.assertEqual(alert["type"], "alert")

    def test_invalid_images_and_processing_errors_return_error_messages(self):
        async def run_invalid_image():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=b"not an image")
            response = await communicator.receive_json_from()
            await communicator.disconnect()
            return response

        invalid_response = async_to_sync(run_invalid_image)()
        self.assertEqual(invalid_response["type"], "error")
        self.assertFalse(invalid_response["success"])

        async def run_processing_error():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=self.image_bytes)
            response = await communicator.receive_json_from()
            await communicator.disconnect()
            return response

        with self.assertLogs("detection_ws.consumers", level="ERROR"):
            with patch(
                "detection_ws.consumers.process_image",
                side_effect=RuntimeError("test failure"),
            ):
                error_response = async_to_sync(run_processing_error)()
        self.assertEqual(error_response["code"], "processing_error")

    def test_image_size_limit_is_enforced(self):
        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=self.image_bytes)
            response = await communicator.receive_json_from()
            await communicator.disconnect()
            return response

        with override_settings(WEBSOCKET_MAX_IMAGE_SIZE=10):
            response = async_to_sync(run)()
        self.assertEqual(response["type"], "error")
        self.assertIn("size limit", response["message"])

    def test_busy_detector_rejects_without_queueing(self):
        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=self.image_bytes)
            response = await communicator.receive_json_from()
            await communicator.disconnect()
            return response

        with patch(
            "detection_ws.consumers.process_image",
            side_effect=inference.DetectorBusyError("busy"),
        ):
            response = async_to_sync(run)()
        self.assertEqual(response["code"], "busy")

    def test_pixel_limit_is_checked_before_decoding(self):
        oversized_header = (
            b"\x89PNG\r\n\x1a\n"
            + (13).to_bytes(4, "big")
            + b"IHDR"
            + (1000).to_bytes(4, "big")
            + (1000).to_bytes(4, "big")
        )

        async def run():
            communicator = self.communicator(self.token)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_to(bytes_data=oversized_header)
            response = await communicator.receive_json_from()
            await communicator.disconnect()
            return response

        with override_settings(WEBSOCKET_MAX_IMAGE_PIXELS=100):
            response = async_to_sync(run)()
        self.assertIn("pixel limit", response["message"])

    def test_simultaneous_connections_are_supported(self):
        async def run():
            first = self.communicator(self.token)
            second = self.communicator(self.token)
            results = await asyncio.gather(first.connect(), second.connect())
            await asyncio.gather(first.disconnect(), second.disconnect())
            return results

        results = async_to_sync(run)()
        self.assertTrue(all(result[0] for result in results))



def make_video(frames=10, fps=5, size=(32, 32)):
    """Encode a tiny MP4 (mp4v) and return its bytes."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp4") as handle:
        writer = cv2.VideoWriter(
            handle.name, cv2.VideoWriter_fourcc(*"mp4v"), fps, size
        )
        for index in range(frames):
            writer.write(np.full((size[1], size[0], 3), index * 20, dtype=np.uint8))
        writer.release()
        return Path(handle.name).read_bytes()


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)
class DetectionUploadTests(TransactionTestCase):
    image_url = "/api/detection/image/"
    video_url = "/api/detection/video/"

    def setUp(self):
        from rest_framework.test import APIClient

        self.user = User.objects.create_user(
            username="rest-user",
            email="rest@example.com",
            password="Secure-password-951!",
        )
        self.token = UnlimitedAccessToken.for_user(self.user)
        IssuedAccessToken.objects.create(jti=self.token["jti"], user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def upload(self, url, field, content, name, **extra):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post(
            url,
            {field: SimpleUploadedFile(name, content), **extra},
            format="multipart",
        )

    def upload_image(self, content=None):
        content = DetectionWebSocketTests.make_png() if content is None else content
        return self.upload(self.image_url, "image", content, "image.png")

    def upload_video(self, content=None, **extra):
        content = make_video() if content is None else content
        return self.upload(self.video_url, "video", content, "clip.mp4", **extra)

    def test_requires_authentication(self):
        from rest_framework.test import APIClient

        for url in (self.image_url, self.video_url):
            response = APIClient().post(url, {}, format="multipart")
            self.assertEqual(response.status_code, 401)

    def test_image_returns_annotation_and_detections(self):
        with patch.object(inference, "load_model", return_value=FakeModel()):
            response = self.upload_image()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["alert"])
        self.assertTrue(body["data"]["upload_id"])
        self.assertEqual(body["data"]["detections"][0]["label"], "violation")
        self.assertTrue(base64.b64decode(body["data"]["image"]))

    def test_missing_or_invalid_image_returns_400(self):
        response = self.client.post(self.image_url, {}, format="multipart")
        self.assertEqual(response.status_code, 400)
        response = self.upload_image(b"not an image")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_image")

    def test_busy_detector_returns_503(self):
        with patch(
            "detection_ws.views.process_image",
            side_effect=inference.DetectorBusyError("busy"),
        ):
            response = self.upload_image()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "busy")

    def test_video_is_sampled_and_returns_violations(self):
        with patch.object(inference, "load_model", return_value=FakeModel()):
            response = self.upload_video(sample_fps="1")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["video"]["fps"], 5.0)
        # 10 frames at 5 fps sampled at 1 fps -> frames 0 and 5.
        self.assertEqual(data["frames_analyzed"], 2)
        self.assertEqual([v["frame"] for v in data["violations"]], [0, 5])
        self.assertEqual(data["violations"][1]["timestamp"], 1.0)
        self.assertEqual(data["summary"], {"violation": 2})
        self.assertTrue(data["alert"])
        self.assertTrue(base64.b64decode(data["violations"][0]["image"]))

    def test_annotated_video_frames_are_capped(self):
        with override_settings(DETECTION_VIDEO_MAX_ANNOTATED_FRAMES=1):
            with patch.object(inference, "load_model", return_value=FakeModel()):
                response = self.upload_video(sample_fps="5")
        violations = response.json()["data"]["violations"]
        self.assertEqual(len(violations), 10)
        self.assertIsNotNone(violations[0]["image"])
        self.assertIsNone(violations[1]["image"])

    def test_invalid_videos_return_400(self):
        response = self.client.post(self.video_url, {}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_video")

        response = self.upload_video(b"not a video at all")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_video")

        with override_settings(DETECTION_MAX_VIDEO_DURATION=1):
            response = self.upload_video()
        self.assertEqual(response.status_code, 400)
        self.assertIn("duration", response.json()["message"])

        with override_settings(DETECTION_MAX_VIDEO_SIZE=10):
            response = self.upload_video()
        self.assertEqual(response.status_code, 400)

        response = self.upload_video(sample_fps="0")
        self.assertEqual(response.status_code, 400)

    def test_uploads_push_alerts_to_the_users_websocket(self):
        from asgiref.sync import sync_to_async

        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/detection/",
                headers=[(b"origin", b"http://localhost:3000")],
                subprotocols=["bearer", str(self.token)],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            image_response = await sync_to_async(self.upload_image)()
            image_alert = await communicator.receive_json_from(timeout=5)
            video_response = await sync_to_async(self.upload_video)(sample_fps="1")
            messages = [
                await communicator.receive_json_from(timeout=5) for _ in range(3)
            ]
            await communicator.disconnect()
            return image_response, image_alert, video_response, messages

        with patch.object(inference, "load_model", return_value=FakeModel()):
            image_response, image_alert, video_response, messages = async_to_sync(
                run
            )()

        self.assertEqual(image_alert["type"], "alert")
        self.assertEqual(image_alert["source"], "image_upload")
        self.assertEqual(
            image_alert["upload_id"], image_response.json()["data"]["upload_id"]
        )
        video_id = video_response.json()["data"]["upload_id"]
        self.assertEqual([m["type"] for m in messages], ["alert", "alert", "video_completed"])
        self.assertEqual(messages[1]["source"], "video_upload")
        self.assertEqual(messages[1]["data"]["timestamp"], 1.0)
        self.assertTrue(all(m["upload_id"] == video_id for m in messages))
        self.assertEqual(messages[2]["data"]["summary"], {"violation": 2})
