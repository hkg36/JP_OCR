import ocr
import json
from pathlib import Path
import config
import huggingface_hub

huggingface_hub.login(config.huggingface_token)

TEST_DATA_ROOT = Path(__file__).parent / "test_data"

mocr = ocr.MangaOcr(local_files_only=False,force_cpu=True)

expected_results = json.loads((TEST_DATA_ROOT / "expected_results.json").read_text(encoding="utf-8"))

for item in expected_results:
    result = mocr(TEST_DATA_ROOT / "images" / item["filename"])
    print(f"File: {item['filename']}")
    print(f"Expected: {item['result']}")
    print(f"Result: {result}")
    assert result == item["result"]