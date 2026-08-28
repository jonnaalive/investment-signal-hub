import argparse
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path)
    parser.add_argument("--output", type=Path, default=Path("config/canon_index.json"))
    args = parser.parse_args()
    text = args.board.read_text(encoding="utf-8")
    pairs = re.findall(r"NEW_FACTORY_([A-Za-z0-9]+)_(20[0-9]{6})", text)
    index = {ticker.upper(): f"{date[:4]}-{date[4:6]}-{date[6:]}" for ticker, date in pairs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"canon index: {len(index)} tickers")


if __name__ == "__main__":
    main()
