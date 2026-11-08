"""Check that incomplete model downloads cannot become active models."""

from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "devops"))
from download_nemotron import MODEL_NAME, download_model


class ModelDownloadTests(unittest.TestCase):
    def test_download_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            response = BytesIO(b"model data")
            response.headers = {"Content-Length": "10"}
            target = download_model(Path(directory), lambda *args, **kwargs: response)
            self.assertEqual(target.read_bytes(), b"model data")
            download_model(Path(directory), lambda *args, **kwargs: self.fail("Downloaded twice"))

    def test_incomplete_download_does_not_publish_model(self):
        with tempfile.TemporaryDirectory() as directory:
            response = BytesIO(b"short")
            response.headers = {"Content-Length": "100"}
            with self.assertRaises(ValueError):
                download_model(Path(directory), lambda *args, **kwargs: response)
            self.assertFalse((Path(directory) / MODEL_NAME).exists())
