"""Export one real color-image result and place it beside the untouched source."""

from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("source")
    parser.add_argument("output_png")
    parser.add_argument("comparison_png")
    args = parser.parse_args()

    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        page.evaluate(
            "() => document.body.insertAdjacentHTML('beforeend', '<input id=\"colorCompareInput\" type=\"file\">')"
        )
        page.locator("#colorCompareInput").set_input_files(args.source)
        result = page.evaluate(
            """
            async () => {
              const file = document.querySelector('#colorCompareInput').files[0];
              const image = await loadImageFile(file);
              const scale = Math.min(1, 900 / Math.max(image.naturalWidth, image.naturalHeight));
              const width = Math.max(1, Math.round(image.naturalWidth * scale));
              const height = Math.max(1, Math.round(image.naturalHeight * scale));
              const canvas = mkCanvas(width, height);
              const context = canvas.getContext('2d', {willReadFrequently:true});
              context.imageSmoothingEnabled = true;
              context.imageSmoothingQuality = 'high';
              context.drawImage(image, 0, 0, width, height);
              const analyzed = analyze(context.getImageData(0, 0, width, height), width, height, 0.012, true);
              const normalized = renormAlpha(analyzed.alpha, 512, width, height,
                analyzed.coverageColor ? 0.025 : 0.12);
              if (analyzed.mode === 'color') regularizeColorContour(normalized, 512, 512);
              const glyph = buildGlyphs(normalized, 512);
              let alphaMass = 0, halfAlpha = 0, opaque = 0, nonzero = 0;
              let totalVariation = 0;
              for (let index = 0; index < normalized.length; index++) {
                const alpha = normalized[index];
                alphaMass += alpha;
                if (alpha > 0) nonzero++;
                if (alpha >= 0.5) halfAlpha++;
                if (alpha >= 0.999) opaque++;
                const x = index % 512, y = (index / 512) | 0;
                if (x) totalVariation += Math.abs(alpha - normalized[index - 1]);
                if (y) totalVariation += Math.abs(alpha - normalized[index - 512]);
              }
              return {dark:glyph.dark, mode:analyzed.mode, hue:analyzed.hue,
                coverageColor:!!analyzed.coverageColor, width, height,
                alphaMass, halfAlpha, opaque, nonzero, totalVariation};
            }
            """
        )
        browser.close()

    if errors:
        raise SystemExit("browser errors: " + "; ".join(errors))
    encoded = result.pop("dark").split(",", 1)[1]
    output_path = Path(args.output_png)
    output_path.write_bytes(base64.b64decode(encoded))

    source_full = Image.open(args.source).convert("RGB")
    pixels = np.asarray(source_full).astype(np.float32)
    red, green, blue = np.moveaxis(pixels, 2, 0)
    # Independent target-specific model: on neutral gray paper, green pigment
    # coverage is proportional to G - (R+B)/2. This deliberately does not reuse
    # the page's hue/chroma implementation.
    green_excess = green - (red + blue) * 0.5
    ink_values = green_excess[green_excess > 8]
    if not ink_values.size:
        raise SystemExit("no green reference pixels")
    solid = float(np.percentile(ink_values, 70))
    reference = np.clip((green_excess - 3) / max(8, solid - 3), 0, 1) ** 0.85
    support = np.asarray(
        Image.fromarray(np.uint8((reference > 0.18) * 255)).filter(ImageFilter.MaxFilter(17))
    ) > 0
    reference = np.where(support, reference, 0)
    ys, xs = np.where(reference > 0.025)
    reference_crop = Image.fromarray(
        np.uint8(reference[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1] * 255), "L"
    )
    reference_scale = 426 / max(reference_crop.size)
    reference_size = (
        round(reference_crop.width * reference_scale),
        round(reference_crop.height * reference_scale),
    )
    reference_alpha = np.asarray(
        reference_crop.resize(reference_size, Image.Resampling.LANCZOS).filter(
            ImageFilter.GaussianBlur(0.55)
        )
    ).astype(np.float32) / 255
    reference_mass = float(reference_alpha.sum())
    reference_half = int(np.count_nonzero(reference_alpha >= 0.5))
    result["greenReferenceMass"] = reference_mass
    result["greenReferenceHalf"] = reference_half
    result["massRatioToGreenReference"] = result["alphaMass"] / reference_mass
    result["halfRatioToGreenReference"] = result["halfAlpha"] / reference_half
    if not 0.90 <= result["massRatioToGreenReference"] <= 1.12:
        raise SystemExit("generated optical mass does not match independent green reference")
    if not 0.90 <= result["halfRatioToGreenReference"] <= 1.12:
        raise SystemExit("generated core area does not match independent green reference")
    # This target-specific window distinguishes the chosen light anti-alias pass
    # from both v16's visibly soft sigma=1.25 result (~4268) and the unsmoothed,
    # paper-texture-following contour (~4580).
    if not 4_400 <= result["totalVariation"] <= 4_525:
        raise SystemExit("generated contour is either blurred or insufficiently regularized")

    source = source_full.copy()
    source.thumbnail((512, 512), Image.Resampling.LANCZOS)
    source_panel = Image.new("RGB", (512, 512), "white")
    source_panel.paste(source, ((512 - source.width) // 2, (512 - source.height) // 2))
    generated = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
    generated_panel = Image.new("RGBA", (512, 512), "white")
    generated_panel.alpha_composite(generated)
    sheet = Image.new("RGB", (1044, 552), (238, 238, 238))
    sheet.paste(source_panel, (0, 40))
    sheet.paste(generated_panel.convert("RGB"), (532, 40))
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 12), "ORIGINAL: green ink on gray textured paper", fill="black")
    draw.text((544, 12), "GENERATED: optical-coverage black alpha", fill="black")
    sheet.save(args.comparison_png)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
