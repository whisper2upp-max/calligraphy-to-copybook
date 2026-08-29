"""Inspect source ink/paper colour separation for the four strict recall fixtures."""

from __future__ import annotations

import argparse
import json
from collections import Counter, deque
from pathlib import Path

from PIL import Image


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values.sort()
    return values[min(len(values) - 1, round((len(values) - 1) * q))]


def components(mask: bytearray, width: int, height: int) -> list[dict[str, object]]:
    seen = bytearray(len(mask))
    found: list[dict[str, object]] = []
    for start, enabled in enumerate(mask):
        if not enabled or seen[start]:
            continue
        queue = deque([start])
        seen[start] = 1
        area = 0
        x0, y0, x1, y1 = width, height, -1, -1
        while queue:
            index = queue.popleft()
            x, y = index % width, index // width
            area += 1
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for yy in range(max(0, y - 1), min(height, y + 2)):
                for xx in range(max(0, x - 1), min(width, x + 2)):
                    nxt = yy * width + xx
                    if mask[nxt] and not seen[nxt]:
                        seen[nxt] = 1
                        queue.append(nxt)
        found.append({"area": area, "box": [x0, y0, x1, y1]})
    return sorted(found, key=lambda item: int(item["area"]), reverse=True)


def inspect(path: Path, summary: bool = False) -> dict[str, object]:
    image = Image.open(path).convert("RGB")
    scale = min(1.0, 900 / max(image.size))
    size = (round(image.width * scale), round(image.height * scale))
    image = image.resize(size, Image.Resampling.LANCZOS)
    pixels = list(image.getdata())
    luminances = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in pixels]
    chromas = [max(rgb) - min(rgb) for rgb in pixels]
    if summary:
        return {
            "file": path.name,
            "size": list(image.size),
            "luminance_quantiles": {
                str(q): round(percentile(luminances.copy(), q), 1)
                for q in (0.001, 0.005, 0.01, 0.02, 0.05, 0.10, 0.25, 0.50, 0.80, 0.95)
            },
            "chroma_quantiles": {
                str(q): round(percentile(chromas.copy(), q), 1)
                for q in (0.50, 0.80, 0.90, 0.95, 0.99)
            },
        }
    bins: list[dict[str, object]] = []
    for low in range(0, 256, 20):
        selected = [chromas[i] for i, lum in enumerate(luminances) if low <= lum < low + 20]
        if selected:
            bins.append(
                {
                    "lum": [low, low + 19],
                    "count": len(selected),
                    "chroma_p10": round(percentile(selected.copy(), 0.10), 1),
                    "chroma_p50": round(percentile(selected.copy(), 0.50), 1),
                    "chroma_p90": round(percentile(selected.copy(), 0.90), 1),
                }
            )
    masks: dict[str, bytearray] = {}
    for threshold in (60, 80, 100, 120, 140, 160, 180, 200, 210, 220):
        masks[f"lum{threshold}"] = bytearray(lum <= threshold for lum in luminances)
        masks[f"neutral{threshold}"] = bytearray(
            lum <= threshold and chromas[i] <= max(16, (threshold - lum) * 0.28 + 12)
            for i, lum in enumerate(luminances)
        )
    mask_report = {}
    for name, mask in masks.items():
        found = components(mask, image.width, image.height)
        mask_report[name] = {
            "pixels": sum(mask),
            "components_ge_4": sum(1 for item in found if int(item["area"]) >= 4),
            "largest": found[:20],
        }
    colours = Counter(pixels)
    return {
        "file": path.name,
        "size": list(image.size),
        "luminance_quantiles": {
            str(q): round(percentile(luminances.copy(), q), 1)
            for q in (0.001, 0.005, 0.01, 0.02, 0.05, 0.10, 0.25, 0.50)
        },
        "chroma_quantiles": {
            str(q): round(percentile(chromas.copy(), q), 1)
            for q in (0.50, 0.80, 0.90, 0.95, 0.99)
        },
        "bins": bins,
        "most_common_colours": [[list(rgb), count] for rgb, count in colours.most_common(12)],
        "masks": mask_report,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    print(json.dumps([inspect(Path(item), args.summary) for item in args.images], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
