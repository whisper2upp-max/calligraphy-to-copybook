"""Source-stroke recall audit with configurable dark/faint ink thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("screenshot")
    parser.add_argument("images", nargs="+")
    parser.add_argument("--assert-recall", type=float)
    parser.add_argument("--luminance-cutoff", type=float, default=80)
    parser.add_argument("--min-chroma", type=float, default=0)
    parser.add_argument("--max-chroma", type=float, default=255)
    parser.add_argument("--hue-center", type=float)
    parser.add_argument("--hue-tolerance", type=float, default=30)
    parser.add_argument("--min-component-area", type=int, default=100)
    parser.add_argument(
        "--alpha-cutoff",
        type=float,
        default=0.12,
        help="Output alpha considered recalled; lower values audit genuine translucent color fringes.",
    )
    parser.add_argument("--exclude-edge-components", action="store_true")
    parser.add_argument("--reference-inset-ratio", type=float, default=0)
    parser.add_argument(
        "--bbox-tolerance",
        type=int,
        default=0,
        help="Allow this many source pixels of endpoint shortfall when checking the output box.",
    )
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1640, "height": 1200})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("document.querySelector('#pages')?.firstElementChild")
        page.evaluate(
            """
            () => {
              for (const child of [...document.body.children]) child.style.display = 'none';
              document.body.insertAdjacentHTML('beforeend', '<input id="blackAuditInput" type="file" multiple>'
                + '<main id="blackAudit" style="display:grid;grid-template-columns:150px repeat(4,300px);gap:14px;padding:20px;background:#eee;align-items:start"></main>');
            }
            """
        )
        page.locator("#blackAuditInput").set_input_files(args.images)
        page.evaluate(
            """settings => { window.__strokeAuditSettings = settings; }""",
            {
                "cutoff": args.luminance_cutoff,
                "minChroma": args.min_chroma,
                "maxChroma": args.max_chroma,
                "hueCenter": args.hue_center,
                "hueTolerance": args.hue_tolerance,
                "minArea": args.min_component_area,
                "alphaCutoff": args.alpha_cutoff,
                "excludeEdge": args.exclude_edge_components,
                "insetRatio": args.reference_inset_ratio,
                "bboxTolerance": args.bbox_tolerance,
            },
        )
        metrics = page.evaluate(
            """
            async () => {
              const files = [...document.querySelector('#blackAuditInput').files];
              const main = document.querySelector('#blackAudit');
              const heading = text => {
                const element = document.createElement('strong');
                element.textContent = text;
                element.style.cssText = 'font:600 16px system-ui;padding:8px';
                main.appendChild(element);
              };
              ['图片', '原图', '原图笔画基准', '当前输出', '漏检红标'].forEach(heading);
              const output = [];
              for (const file of files) {
                const image = await loadImageFile(file);
                const scale = Math.min(1, 900 / Math.max(image.naturalWidth, image.naturalHeight));
                const width = Math.max(1, Math.round(image.naturalWidth * scale));
                const height = Math.max(1, Math.round(image.naturalHeight * scale));
                const source = mkCanvas(width, height);
                const context = source.getContext('2d', {willReadFrequently:true});
                context.imageSmoothingEnabled = true;
                context.imageSmoothingQuality = 'high';
                context.drawImage(image, 0, 0, width, height);
                const imageData = context.getImageData(0, 0, width, height);
                const pixels = imageData.data, count = width * height;

                const audit = window.__strokeAuditSettings;
                /* Reference pixels are grouped with 8-connectivity and only sizeable parts
                   are counted. A higher configurable cutoff covers pale dry-brush strokes. */
                const candidate = new Uint8Array(count);
                const insetX = Math.round(width * audit.insetRatio);
                const insetY = Math.round(height * audit.insetRatio);
                for (let i = 0, j = 0; i < count; i++, j += 4) {
                  const x = i % width, y = Math.floor(i / width);
                  const lum = 0.2126 * pixels[j] + 0.7152 * pixels[j + 1] + 0.0722 * pixels[j + 2];
                  const r = pixels[j], g = pixels[j + 1], b = pixels[j + 2];
                  const hi = Math.max(r, g, b), lo = Math.min(r, g, b), chroma = hi - lo;
                  let hue = 0;
                  if (chroma) {
                    if (hi === r) hue = (((g - b) / chroma) % 6) * 60;
                    else if (hi === g) hue = ((b - r) / chroma + 2) * 60;
                    else hue = ((r - g) / chroma + 4) * 60;
                    if (hue < 0) hue += 360;
                  }
                  const hueDistance = audit.hueCenter === null
                    ? 0 : Math.abs(((hue - audit.hueCenter + 540) % 360) - 180);
                  const inside = x >= insetX && x < width - insetX && y >= insetY && y < height - insetY;
                  candidate[i] = inside && lum <= audit.cutoff
                    && chroma >= audit.minChroma && chroma <= audit.maxChroma
                    && (audit.hueCenter === null || hueDistance <= audit.hueTolerance) ? 1 : 0;
                }
                const labels = new Int32Array(count), queue = new Int32Array(count);
                const components = [{area:0, box:[0,0,0,0]}];
                let label = 0;
                for (let start = 0; start < count; start++) {
                  if (!candidate[start] || labels[start]) continue;
                  label++;
                  let head = 0, tail = 0, area = 0;
                  let x0 = width, y0 = height, x1 = -1, y1 = -1;
                  labels[start] = label; queue[tail++] = start;
                  while (head < tail) {
                    const index = queue[head++], x = index % width, y = Math.floor(index / width);
                    area++; x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
                    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
                      if (!dx && !dy) continue;
                      const xx = x + dx, yy = y + dy;
                      if (xx < 0 || xx >= width || yy < 0 || yy >= height) continue;
                      const next = yy * width + xx;
                      if (candidate[next] && !labels[next]) { labels[next] = label; queue[tail++] = next; }
                    }
                  }
                  components[label] = {area, box:[x0,y0,x1,y1]};
                }
                const reference = new Uint8Array(count);
                const eligible = component => component.area >= audit.minArea
                  && (!audit.excludeEdge || (component.box[0] > 0 && component.box[1] > 0
                    && component.box[2] < width - 1 && component.box[3] < height - 1));
                for (let i = 0; i < count; i++) if (labels[i] && eligible(components[labels[i]])) reference[i] = 1;

                const result = analyze(imageData, width, height, 0.012, true);
                const variants = [
                  ['grid-r012', result],
                  ['grid-no-filter', analyze(imageData, width, height, 0.000001, true)],
                  ['no-grid-r012', analyze(imageData, width, height, 0.012, false)],
                  ['no-grid-no-filter', analyze(imageData, width, height, 0.000001, false)],
                ];
                let referencePixels = 0, recalled = 0, missed = 0;
                const missedPixels = [];
                const perComponent = [];
                for (let current = 1; current < components.length; current++) {
                  const component = components[current];
                  if (!eligible(component)) continue;
                  let kept = 0;
                  for (let i = 0; i < count; i++) if (labels[i] === current && result.alpha[i] > audit.alphaCutoff) kept++;
                  perComponent.push({area:component.area, box:component.box, recalled:kept, recall:kept/component.area});
                  referencePixels += component.area; recalled += kept;
                }
                missed = referencePixels - recalled;
                for (let i = 0; i < count && missedPixels.length < 50; i++) if (reference[i] && result.alpha[i] <= audit.alphaCutoff) {
                  const j = i * 4;
                  missedPixels.push({
                    x:i % width, y:Math.floor(i / width),
                    luminance:Number((0.2126 * pixels[j] + 0.7152 * pixels[j + 1] + 0.0722 * pixels[j + 2]).toFixed(2)),
                    alpha:Number(result.alpha[i].toFixed(4)),
                  });
                }
                const variantRecall = {};
                for (const [variantName, variant] of variants) {
                  let variantKept = 0;
                  for (let i = 0; i < count; i++) if (reference[i] && variant.alpha[i] > audit.alphaCutoff) variantKept++;
                  variantRecall[variantName] = referencePixels ? variantKept / referencePixels : 1;
                }

                let refX0 = width, refY0 = height, refX1 = -1, refY1 = -1;
                for (let i = 0; i < count; i++) if (reference[i]) {
                  const x = i % width, y = Math.floor(i / width);
                  refX0 = Math.min(refX0, x); refY0 = Math.min(refY0, y);
                  refX1 = Math.max(refX1, x); refY1 = Math.max(refY1, y);
                }
                const outputBoxObject = inkBBox(result.alpha, width, height, audit.alphaCutoff);
                const outputBox = [outputBoxObject.x0, outputBoxObject.y0, outputBoxObject.x1, outputBoxObject.y1];
                const referenceBox = [refX0, refY0, refX1, refY1];
                const boxTolerance = audit.bboxTolerance;
                const containsReference = outputBoxObject.x0 <= refX0 + boxTolerance
                  && outputBoxObject.y0 <= refY0 + boxTolerance
                  && outputBoxObject.x1 >= refX1 - boxTolerance
                  && outputBoxObject.y1 >= refY1 - boxTolerance;

                const maskCanvas = mkCanvas(width, height), maskContext = maskCanvas.getContext('2d');
                const maskData = maskContext.createImageData(width, height);
                const normalized = renormAlpha(result.alpha, 512, width, height, audit.alphaCutoff);
                const alphaCanvas = mkCanvas(512, 512), alphaContext = alphaCanvas.getContext('2d');
                const alphaData = alphaContext.createImageData(512, 512);
                const missCanvas = mkCanvas(width, height), missContext = missCanvas.getContext('2d');
                missContext.drawImage(source, 0, 0);
                const missData = missContext.getImageData(0, 0, width, height);
                for (let i = 0, j = 0; i < count; i++, j += 4) {
                  if (reference[i]) {
                    maskData.data[j] = maskData.data[j + 1] = maskData.data[j + 2] = 20;
                    maskData.data[j + 3] = 255;
                  }
                  if (reference[i] && result.alpha[i] <= audit.alphaCutoff) {
                    missData.data[j] = 255; missData.data[j + 1] = 0; missData.data[j + 2] = 0; missData.data[j + 3] = 255;
                  }
                }
                for (let i = 0, j = 0; i < normalized.length; i++, j += 4) if (normalized[i] > audit.alphaCutoff) {
                  alphaData.data[j] = alphaData.data[j + 1] = alphaData.data[j + 2] = 20;
                  alphaData.data[j + 3] = 255;
                }
                maskContext.putImageData(maskData, 0, 0);
                alphaContext.putImageData(alphaData, 0, 0);
                missContext.putImageData(missData, 0, 0);

                const labelCell = document.createElement('div');
                labelCell.textContent = `${file.name}\n${recalled}/${referencePixels}\n${(100*recalled/referencePixels).toFixed(3)}%`;
                labelCell.style.cssText = 'white-space:pre-wrap;font:14px/1.45 monospace;padding:8px';
                main.appendChild(labelCell);
                for (const canvas of [source, maskCanvas, alphaCanvas, missCanvas]) {
                  canvas.style.cssText = 'width:300px;height:300px;object-fit:contain;background:#fff';
                  main.appendChild(canvas);
                }
                output.push({
                  file:file.name, width, height, mode:result.mode,
                  referencePixels, recalled, missed,
                  missedPixels,
                  recall:referencePixels ? recalled/referencePixels : 1,
                  variantRecall,
                  referenceBox, outputBox, containsReference,
                  components:perComponent.sort((a,b) => b.area-a.area),
                });
              }
              return output;
            }
            """
        )
        page.screenshot(path=args.screenshot, full_page=True)
        browser.close()

    report = {
        "errors": errors,
        "settings": {
            "luminance_cutoff": args.luminance_cutoff,
            "min_chroma": args.min_chroma,
            "max_chroma": args.max_chroma,
            "hue_center": args.hue_center,
            "hue_tolerance": args.hue_tolerance,
            "min_component_area": args.min_component_area,
            "alpha_cutoff": args.alpha_cutoff,
            "exclude_edge_components": args.exclude_edge_components,
            "reference_inset_ratio": args.reference_inset_ratio,
            "bbox_tolerance": args.bbox_tolerance,
        },
        "images": metrics,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit("browser errors during black recall audit")
    if args.assert_recall is not None:
        failed = [item for item in metrics if item["recall"] < args.assert_recall]
        if failed:
            raise SystemExit(
                "strict source-stroke recall below threshold: "
                + ", ".join(f"{item['file']}={item['recall']:.6f}" for item in failed)
            )
        not_contained = [item for item in metrics if not item["containsReference"]]
        if not_contained:
            raise SystemExit(
                "strict source-stroke reference falls outside normalization bbox: "
                + ", ".join(item["file"] for item in not_contained)
            )


if __name__ == "__main__":
    main()
