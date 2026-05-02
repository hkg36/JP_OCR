import ocr
import json
from pathlib import Path
import huggingface_hub
import sys
import yaml

not_network = False
if len(sys.argv) > 1 and sys.argv[1] == "--not-network":
    not_network = True

with open("conf.yaml", "r", encoding="utf-8") as f:
    conf = yaml.safe_load(f)

if not not_network:
    huggingface_hub.login(conf["key"]["hf_token"])

TEST_DATA_ROOT = Path(__file__).parent / "test_data"

mocr = ocr.MangaOcr(local_files_only=not_network,force_cpu=True)

expected_results = json.loads((TEST_DATA_ROOT / "expected_results.json").read_text(encoding="utf-8"))

for item in expected_results:
    result = mocr(TEST_DATA_ROOT / "images" / item["filename"])
    print(f"File: {item['filename']}")
    print(f"Expected: {item['result']}")
    print(f"Result: {result}")
    assert result == item["result"]