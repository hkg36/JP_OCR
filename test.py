import ocr
import json
from pathlib import Path

TEST_DATA_ROOT = Path(__file__).parent / "test_data"

mocr = ocr.MangaOcr(local_files_only=False)

expected_results = json.loads((TEST_DATA_ROOT / "expected_results.json").read_text(encoding="utf-8"))

for item in expected_results:
    result = mocr(TEST_DATA_ROOT / "images" / item["filename"])
    print(f"File: {item['filename']}")
    print(f"Expected: {item['result']}")
    print(f"Result: {result}")
    assert result == item["result"]