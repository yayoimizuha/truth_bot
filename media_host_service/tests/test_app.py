import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import av
from fastapi.testclient import TestClient
from PIL import Image

from app import app


class MediaHostServiceTests(unittest.TestCase):
    @staticmethod
    def _png_bytes(color: tuple[int, int, int]) -> bytes:
        image = Image.new("RGB", (64, 64), color=color)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _mp4_bytes(color: tuple[int, int, int]) -> bytes:
        output = BytesIO()
        image = Image.new("RGB", (64, 64), color=color)
        with av.open(output, mode="w", format="mp4") as container:
            stream = container.add_stream("mpeg4", rate=1)
            stream.width = 64
            stream.height = 64
            stream.pix_fmt = "yuv420p"
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        return output.getvalue()

    def test_upload_returns_public_page_and_ogp_page(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MEDIA_HOST_STORAGE_DIR": temp_dir},
            clear=False,
        ):
            client = TestClient(app)
            response = client.post(
                "/media",
                files=[("files", ("cat.png", self._png_bytes((255, 0, 0)), "image/png"))],
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("/m/", payload["public_url"])
            page_id = payload["page_id"]

            page_response = client.get(payload["public_url"])
            self.assertEqual(page_response.status_code, 200)
            self.assertIn(f"/og/{page_id}.png", page_response.text)
            self.assertIn("<img", page_response.text)

            metadata_path = Path(temp_dir) / page_id / "metadata.json"
            self.assertTrue(metadata_path.exists())

            ogp_response = client.get(f"/og/{page_id}.png")
            self.assertEqual(ogp_response.status_code, 200)
            self.assertEqual(ogp_response.headers["content-type"], "image/png")
            ogp_image = Image.open(BytesIO(ogp_response.content))
            self.assertEqual(ogp_image.size, (1200, 630))

    def test_og_image_combines_multiple_images(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MEDIA_HOST_STORAGE_DIR": temp_dir},
            clear=False,
        ):
            client = TestClient(app)
            response = client.post(
                "/media",
                files=[
                    ("files", ("one.png", self._png_bytes((255, 0, 0)), "image/png")),
                    ("files", ("two.png", self._png_bytes((0, 255, 0)), "image/png")),
                ],
            )

            self.assertEqual(response.status_code, 200)
            page_id = response.json()["page_id"]

            ogp_response = client.get(f"/og/{page_id}.png")
            self.assertEqual(ogp_response.status_code, 200)
            image = Image.open(BytesIO(ogp_response.content)).convert("RGB")
            self.assertEqual(image.size, (1200, 630))
            left_pixel = image.getpixel((200, 315))
            right_pixel = image.getpixel((1000, 315))
            self.assertGreater(left_pixel[0], left_pixel[1])
            self.assertGreater(right_pixel[1], right_pixel[0])

    def test_upload_video_renders_video_tag_and_uses_poster_for_og_image(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MEDIA_HOST_STORAGE_DIR": temp_dir},
            clear=False,
        ):
            client = TestClient(app)
            response = client.post(
                "/media",
                files=[("files", ("clip.mp4", self._mp4_bytes((0, 0, 255)), "video/mp4"))],
            )

            self.assertEqual(response.status_code, 200)
            page_id = response.json()["page_id"]

            page_response = client.get(f"/m/{page_id}")
            self.assertEqual(page_response.status_code, 200)
            self.assertIn("<video", page_response.text)
            self.assertIn(".poster.png", page_response.text)

            metadata = (Path(temp_dir) / page_id / "metadata.json").read_text()
            self.assertIn('"kind": "video"', metadata)
            self.assertIn('"poster_filename"', metadata)

            ogp_response = client.get(f"/og/{page_id}.png")
            self.assertEqual(ogp_response.status_code, 200)
            ogp_image = Image.open(BytesIO(ogp_response.content)).convert("RGB")
            self.assertEqual(ogp_image.size, (1200, 630))
            pixel = ogp_image.getpixel((600, 315))
            self.assertGreater(pixel[2], pixel[0])

    def test_rejects_unsupported_media_type(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MEDIA_HOST_STORAGE_DIR": temp_dir},
            clear=False,
        ):
            client = TestClient(app)
            response = client.post(
                "/media",
                files=[("files", ("note.txt", b"hello", "text/plain"))],
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "only image and video files are supported")

    def test_upload_requires_auth_only_when_password_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MEDIA_HOST_STORAGE_DIR": temp_dir, "MEDIA_HOST_UPLOAD_PASSWORD": "secret"},
            clear=False,
        ):
            client = TestClient(app)

            unauthorized = client.post(
                "/media",
                files=[("files", ("cat.png", self._png_bytes((255, 0, 0)), "image/png"))],
            )
            self.assertEqual(unauthorized.status_code, 401)

            authorized = client.post(
                "/media",
                files=[("files", ("cat.png", self._png_bytes((255, 0, 0)), "image/png"))],
                auth=("upload", "secret"),
            )
            self.assertEqual(authorized.status_code, 200)

            page_id = authorized.json()["page_id"]
            page_response = client.get(f"/m/{page_id}")
            self.assertEqual(page_response.status_code, 200)

    def test_page_json_returns_media_items(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"MEDIA_HOST_STORAGE_DIR": temp_dir},
            clear=False,
        ):
            client = TestClient(app)
            response = client.post(
                "/media",
                files=[("files", ("clip.mp4", self._mp4_bytes((0, 0, 255)), "video/mp4"))],
            )
            self.assertEqual(response.status_code, 200)

            page_id = response.json()["page_id"]
            payload = client.get(f"/api/pages/{page_id}").json()
            self.assertEqual(payload["page_id"], page_id)
            self.assertIn("/m/", payload["public_url"])
            self.assertIn("/og/", payload["og_image_url"])
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(payload["items"][0]["kind"], "video")
            self.assertIn("poster_url", payload["items"][0])


if __name__ == "__main__":
    unittest.main()
