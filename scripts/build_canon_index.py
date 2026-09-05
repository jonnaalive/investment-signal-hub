import argparse
import json
import re
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("config/canon_index.json"))
    args = parser.parse_args()
    index = json.loads(args.output.read_text()) if args.output.exists() else {}
    for path in args.board:
        if path.is_dir():
            text = subprocess.run(["rg", "--files", str(path), "-g", "NEW_FACTORY_*.md"], check=True, capture_output=True, text=True).stdout
        else:
            text = path.read_text(encoding="utf-8")
        pairs = re.findall(r"NEW_FACTORY_([A-Za-z0-9.]+)_(20[0-9]{6})", text)
        for ticker, date in pairs:
            value = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            index[ticker.upper()] = max(index.get(ticker.upper(), ""), value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"canon index: {len(index)} tickers")


if __name__ == "__main__":
    main()
