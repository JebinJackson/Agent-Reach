"""Tests for XiaoHongShu image/video enrichment."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_reach import xhs_multimodal
from agent_reach.xhs_multimodal import (
    MAX_MEDIA_BYTES,
    XhsMultimodalError,
    enrich_note,
    extract_media,
    normalize_note_payload,
)


class _FakeResponse:
    """Just enough of requests.Response for redirect and size checks."""

    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise XhsMultimodalError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=0):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestXhsMultimodal(unittest.TestCase):
    NOTE = {
        "note_card": {
            "title": "测试笔记",
            "image_list": [{"url": "https://sns-img.xhscdn.com/image.jpg"}],
            "video_info": {"media": {"play_url": "https://sns-video.xhscdn.com/video.mp4"}},
        }
    }

    def test_extracts_nested_image_and_video_urls(self):
        images, video = extract_media(self.NOTE)
        self.assertEqual(images, ["https://sns-img.xhscdn.com/image.jpg"])
        self.assertEqual(video, "https://sns-video.xhscdn.com/video.mp4")

    def test_normalizes_opencli_field_output(self):
        self.assertEqual(
            normalize_note_payload([{"field": "title", "value": "测试"}]),
            {"title": "测试"},
        )

    @patch("agent_reach.xhs_multimodal._ocr_image", return_value="图片里的文字")
    @patch("agent_reach.xhs_multimodal._download")
    def test_enriches_image_and_video_independently(self, download, ocr):
        download.return_value = Path("/tmp/image.jpg")
        enriched = enrich_note(self.NOTE, transcriber=lambda url: "视频转写")
        self.assertEqual(enriched["multimodal"]["images"][0]["text"], "图片里的文字")
        self.assertEqual(enriched["multimodal"]["video"]["transcript"], "视频转写")
        ocr.assert_called_once()

    @patch("agent_reach.xhs_multimodal._ocr_image", return_value="下载图片文字")
    def test_enriches_downloaded_local_media(self, ocr):
        enriched = enrich_note(
            {"title": "下载的笔记"},
            transcribe_video=False,
            media_files=[Path("/tmp/note_1.jpg")],
        )
        self.assertEqual(enriched["multimodal"]["images"][0]["file"], "note_1.jpg")
        self.assertEqual(enriched["multimodal"]["images"][0]["text"], "下载图片文字")
        ocr.assert_called_once()

    @patch(
        "agent_reach.xhs_multimodal._ocr_image",
        side_effect=XhsMultimodalError("ocr failed"),
    )
    @patch("agent_reach.xhs_multimodal._download")
    def test_image_failure_does_not_drop_note(self, download, _ocr):
        download.return_value = Path("/tmp/image.jpg")
        enriched = enrich_note(self.NOTE, transcribe_video=False)
        self.assertEqual(enriched["note_card"]["title"], "测试笔记")
        self.assertEqual(enriched["multimodal"]["images"][0]["error"], "ocr failed")

    def test_rejects_media_outside_cdn_hosts(self):
        enriched = enrich_note({"images": ["https://evil.test/a.jpg"]}, transcribe_video=False)
        self.assertIn("error", enriched["multimodal"]["images"][0])

    @patch("agent_reach.xhs_multimodal.requests.get")
    def test_checks_redirect_target_before_following(self, get):
        get.return_value = _FakeResponse(302, {"location": "https://evil.test/a.jpg"})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(XhsMultimodalError):
                xhs_multimodal._download("https://sns-img.xhscdn.com/a.jpg", Path(temp))
        get.assert_called_once()

    @patch("agent_reach.xhs_multimodal.requests.get")
    def test_rejects_declared_size_over_limit(self, get):
        get.return_value = _FakeResponse(200, {"content-length": str(MAX_MEDIA_BYTES + 1)})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(XhsMultimodalError):
                xhs_multimodal._download("https://sns-img.xhscdn.com/a.jpg", Path(temp))


if __name__ == "__main__":
    unittest.main()
