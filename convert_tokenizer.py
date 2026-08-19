"""Convert manga-ocr's legacy Japanese tokenizer to tokenizer.json format."""

import argparse
import json
from pathlib import Path

from tokenizers import Regex, Tokenizer
from tokenizers.models import WordPiece
from tokenizers.normalizers import BertNormalizer
from tokenizers.pre_tokenizers import Split
from transformers import BertJapaneseTokenizer, PreTrainedTokenizerFast
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent

with open(PROJECT_ROOT / "conf.yaml", "r", encoding="utf-8") as f:
    conf = yaml.safe_load(f)


def convert(source: str, output: str) -> None:
    """Convert a legacy tokenizer directory into a fast tokenizer directory."""
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)

    slow = BertJapaneseTokenizer.from_pretrained(
        source,
        local_files_only=False,
    )
    vocabulary = dict(sorted(slow.get_vocab().items(), key=lambda item: item[1]))
    backend = Tokenizer(WordPiece(vocabulary, unk_token=slow.unk_token))
    backend.normalizer = BertNormalizer(lowercase=False, handle_chinese_chars=False)
    backend.pre_tokenizer = Split(Regex("."), behavior="isolated", invert=True)

    fast = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token=slow.unk_token,
        sep_token=slow.sep_token,
        pad_token=slow.pad_token,
        cls_token=slow.cls_token,
        mask_token=slow.mask_token,
    )
    fast.save_pretrained(output_path)

    config_path = output_path / "tokenizer_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["tokenizer_class"] = "PreTrainedTokenizerFast"
    config["do_lower_case"] = False
    config["tokenize_chinese_chars"] = False
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Converted tokenizer written to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        default=conf["ocr"]["local_model"],
        help="Legacy model directory or Hugging Face model name (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "tmp" / "newtoken"),
        help="Directory where tokenizer.json will be written (default: %(default)s)",
    )
    args = parser.parse_args()
    convert(args.source, args.output)