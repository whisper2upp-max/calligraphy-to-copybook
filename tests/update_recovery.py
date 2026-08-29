import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace the generated RECOVER payload in the single-file app."
    )
    parser.add_argument("html")
    parser.add_argument("recovery_json")
    args = parser.parse_args()

    html_path = Path(args.html)
    recovery_path = Path(args.recovery_json)
    source = html_path.read_text(encoding="utf-8")
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))

    glyph_dir = html_path.parent / "字"
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    expected = {
        path.stem
        for path in glyph_dir.iterdir()
        if path.is_file() and path.suffix.lower() in image_suffixes
    }
    if not expected:
        raise SystemExit(f"No source images found in {glyph_dir}")
    if set(recovery) != expected:
        missing = sorted(expected - set(recovery))
        extra = sorted(set(recovery) - expected)
        raise SystemExit(f"Unexpected recovery keys; missing={missing}, extra={extra}")
    if not all(
        isinstance(value, str) and value.startswith("data:image/png;base64,")
        for value in recovery.values()
    ):
        raise SystemExit("Recovery payload contains a non-PNG data URL.")

    marker = "const RECOVER = "
    start = source.index(marker) + len(marker)
    end = source.index(";\n", start)
    payload = json.dumps(recovery, ensure_ascii=False, separators=(",", ":"))
    updated = source[:start] + payload + source[end:]
    html_path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
