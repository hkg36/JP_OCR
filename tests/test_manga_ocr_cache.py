import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from huggingface_hub import snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError

from manga_ocr_cpt_lib import MODELS, MangaOcrCPT, resolve


class LocalModelCacheTests(unittest.TestCase):
    def test_default_loading_allows_network(self):
        with patch("huggingface_hub.snapshot_download", return_value="cached-model") as download:
            self.assertEqual(resolve("flex"), Path("cached-model"))
        download.assert_called_once_with(
            MODELS["flex"],
            allow_patterns=["model.safetensors", "config.json", "nar_vocabulary_6817.json"],
            local_files_only=False,
        )

    def test_local_directory_bypasses_hub(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "model.safetensors").touch()
            with patch("huggingface_hub.snapshot_download") as download:
                for local_files_only in (False, True):
                    self.assertEqual(resolve(directory, local_files_only=local_files_only), model_dir)
            download.assert_not_called()

    def test_cached_snapshots_resolve_without_network(self):
        for model in ("flex", "224", "example/custom-model"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                repo = MODELS.get(model, model)
                repo_dir = Path(directory) / ("models--" + repo.replace("/", "--"))
                snapshot = repo_dir / "snapshots" / ("a" * 40)
                snapshot.mkdir(parents=True)
                (repo_dir / "refs").mkdir()
                (repo_dir / "refs" / "main").write_text(snapshot.name, encoding="utf-8")
                with patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access")), \
                     patch("huggingface_hub.snapshot_download",
                           side_effect=lambda *args, **kwargs: snapshot_download(
                               *args, cache_dir=directory, **kwargs)):
                    self.assertEqual(resolve(model, local_files_only=True), snapshot)

    def test_missing_cache_raises_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access")), \
                 patch("huggingface_hub.snapshot_download",
                       side_effect=lambda *args, **kwargs: snapshot_download(
                           *args, cache_dir=directory, **kwargs)):
                with self.assertRaises(LocalEntryNotFoundError):
                    resolve("flex", local_files_only=True)

    def test_constructor_forwards_cache_option(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            (model_dir / "config.json").write_text(json.dumps({"input": {"fit": "aspect"}}), encoding="utf-8")
            (model_dir / "nar_vocabulary_6817.json").write_text(
                json.dumps({"characters": ["a"]}), encoding="utf-8")
            for local_files_only in (False, True):
                with self.subTest(local_files_only=local_files_only), \
                     patch("manga_ocr_cpt_lib.resolve", return_value=model_dir) as resolver, \
                     patch("manga_ocr_cpt_lib.model.MangaOcrNet.load") as load:
                    ocr = MangaOcrCPT("flex", "cpu", "plain", "fp32", local_files_only=local_files_only)
                    resolver.assert_called_once_with("flex", local_files_only=local_files_only)
                    load.assert_called_once_with(model_dir, ocr.device, half_decoder=False)
                    self.assertEqual(ocr.fit, "aspect")
                    self.assertEqual(ocr.chars, ["a"])


if __name__ == "__main__":
    unittest.main()
