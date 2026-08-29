"""Render several final anti-alias strengths from the same extracted color alpha."""

from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("source")
    parser.add_argument("output_sheet")
    args = parser.parse_args()

    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        page.evaluate(
            "() => document.body.insertAdjacentHTML('beforeend', "
            "'<input id=\"sharpnessProbeInput\" type=\"file\">')"
        )
        page.locator("#sharpnessProbeInput").set_input_files(args.source)
        result = page.evaluate(
            """
            async () => {
              const file = document.querySelector('#sharpnessProbeInput').files[0];
              const image = await loadImageFile(file);
              const scale = Math.min(1, 900 / Math.max(image.naturalWidth, image.naturalHeight));
              const width = Math.max(1, Math.round(image.naturalWidth * scale));
              const height = Math.max(1, Math.round(image.naturalHeight * scale));
              const canvas = mkCanvas(width, height);
              const context = canvas.getContext('2d', {willReadFrequently:true});
              context.imageSmoothingEnabled = true;
              context.imageSmoothingQuality = 'high';
              context.drawImage(image, 0, 0, width, height);
              const analyzed = analyze(
                context.getImageData(0, 0, width, height), width, height, 0.012, true
              );
              const base = renormAlpha(
                analyzed.alpha, 512, width, height, analyzed.coverageColor ? 0.025 : 0.12
              );

              const gaussian = (source, sigma) => {
                if (!sigma) return Float32Array.from(source);
                const radius = Math.max(1, Math.ceil(sigma * 2.5));
                const weights = new Float32Array(radius * 2 + 1);
                let total = 0;
                for (let d = -radius; d <= radius; d++) {
                  const value = Math.exp(-(d * d) / (2 * sigma * sigma));
                  weights[d + radius] = value;
                  total += value;
                }
                for (let i = 0; i < weights.length; i++) weights[i] /= total;
                const horizontal = new Float32Array(source.length);
                const output = new Float32Array(source.length);
                for (let y = 0; y < 512; y++) for (let x = 0; x < 512; x++) {
                  let sum = 0, used = 0;
                  for (let d = -radius; d <= radius; d++) {
                    const xx = x + d;
                    if (xx < 0 || xx >= 512) continue;
                    const weight = weights[d + radius];
                    sum += source[y * 512 + xx] * weight;
                    used += weight;
                  }
                  horizontal[y * 512 + x] = sum / used;
                }
                for (let y = 0; y < 512; y++) for (let x = 0; x < 512; x++) {
                  let sum = 0, used = 0;
                  for (let d = -radius; d <= radius; d++) {
                    const yy = y + d;
                    if (yy < 0 || yy >= 512) continue;
                    const weight = weights[d + radius];
                    sum += horizontal[yy * 512 + x] * weight;
                    used += weight;
                  }
                  output[y * 512 + x] = sum / used;
                }
                return output;
              };

              const candidates = [];
              for (const sigma of [0, 0.55, 0.75, 0.95, 1.25]) {
                const alpha = gaussian(base, sigma);
                let alphaMass = 0, halfAlpha = 0, softTail = 0, totalVariation = 0;
                for (let y = 0; y < 512; y++) for (let x = 0; x < 512; x++) {
                  const index = y * 512 + x;
                  const value = alpha[index] >= 0.006 ? alpha[index] : 0;
                  alpha[index] = value;
                  alphaMass += value;
                  if (value >= 0.5) halfAlpha++;
                  if (value > 0 && value < 0.12) softTail++;
                  if (x) totalVariation += Math.abs(value - alpha[index - 1]);
                  if (y) totalVariation += Math.abs(value - alpha[index - 512]);
                }
                candidates.push({
                  sigma,
                  alphaMass,
                  halfAlpha,
                  softTail,
                  totalVariation,
                  dark: buildGlyphs(alpha, 512).dark,
                });
              }
              return {mode: analyzed.mode, coverageColor: !!analyzed.coverageColor, candidates};
            }
            """
        )
        browser.close()

    if errors:
        raise SystemExit("browser errors: " + "; ".join(errors))
    if result["mode"] != "color" or not result["coverageColor"]:
        raise SystemExit(f"unexpected branch: {result['mode']!r}")

    panels: list[Image.Image] = []
    printable = {"mode": result["mode"], "coverageColor": result["coverageColor"], "candidates": []}
    for candidate in result["candidates"]:
        encoded = candidate.pop("dark").split(",", 1)[1]
        panel = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
        white = Image.new("RGBA", (512, 512), "white")
        white.alpha_composite(panel)
        panels.append(white.convert("RGB"))
        printable["candidates"].append(candidate)

    sheet = Image.new("RGB", (len(panels) * 512, 552), (238, 238, 238))
    draw = ImageDraw.Draw(sheet)
    for index, (panel, candidate) in enumerate(zip(panels, printable["candidates"])):
        x = index * 512
        sheet.paste(panel, (x, 40))
        draw.text(
            (x + 12, 12),
            f"sigma={candidate['sigma']:.2f}  mass={candidate['alphaMass']:.0f}  "
            f"core={candidate['halfAlpha']}",
            fill="black",
        )
    Path(args.output_sheet).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output_sheet)
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
