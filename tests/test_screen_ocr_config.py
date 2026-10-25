import unittest
from types import SimpleNamespace
from unittest.mock import patch

import checkSingle

checkSingle.check_single_instance = lambda: None

import manga_ocr_cpt_lib
import screen


class SnippingToolOcrConfigTests(unittest.TestCase):
    def setUp(self):
        self.original_config = screen.GLOBAL_CONFIG

    def tearDown(self):
        screen.GLOBAL_CONFIG = self.original_config

    def test_local_model_config_is_loaded_and_reloaded_on_change(self):
        tool = SimpleNamespace(mocr=None, _mocr_model=None)
        screen.GLOBAL_CONFIG = {
            "ocr": {"server_url": "", "local_model": "owner/custom-model"}
        }

        with patch.object(manga_ocr_cpt_lib, "MangaOcrCPT") as model:
            screen.SnippingTool.OcrConfigChange(tool)
            model.assert_called_once_with("owner/custom-model", local_files_only=True)
            self.assertIs(tool.mocr, model.return_value)

            screen.SnippingTool.OcrConfigChange(tool)
            model.assert_called_once()

            screen.GLOBAL_CONFIG["ocr"]["local_model"] = "flex"
            screen.SnippingTool.OcrConfigChange(tool)
            model.assert_called_with("flex", local_files_only=True)
            self.assertEqual(model.call_count, 2)

            screen.GLOBAL_CONFIG["ocr"]["server_url"] = "http://ocr.local"
            screen.SnippingTool.OcrConfigChange(tool)
            self.assertIsNone(tool.mocr)
            self.assertIsNone(tool._mocr_model)


if __name__ == "__main__":
    unittest.main()
