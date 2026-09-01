import argparse
import json

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guard against treating a uniformly tinted textured paper as colored ink."
    )
    parser.add_argument("base_url")
    parser.add_argument("image")
    parser.add_argument("screenshot")
    args = parser.parse_args()

    page_errors: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1080, "height": 720})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        page.evaluate(
            """
            () => {
              document.body.innerHTML = '<input id="probeInput" type="file">'
                + '<main id="probe" style="display:flex;gap:28px;padding:28px;background:#eee"></main>';
            }
            """
        )
        page.locator("#probeInput").set_input_files(args.image)
        metrics = page.evaluate(
            """
            async () => {
              const file = document.querySelector('#probeInput').files[0];
              const image = await loadImageFile(file);
              const scale = Math.min(1, 900 / Math.max(image.naturalWidth, image.naturalHeight));
              const width = Math.round(image.naturalWidth * scale);
              const height = Math.round(image.naturalHeight * scale);
              const source = mkCanvas(width, height);
              const context = source.getContext('2d', {willReadFrequently: true});
              context.drawImage(image, 0, 0, width, height);
              const imageData = context.getImageData(0, 0, width, height);
              const result = analyze(imageData, width, height, 0.006, true);
              const normalized = renormAlpha(result.alpha, 512, width, height);
              if (!normalized) throw new Error('No glyph was extracted from the textured source.');

              const box = inkBBox(result.alpha, width, height, 0.12);
              const band = Math.max(2, Math.round(Math.min(width, height) * 0.05));
              let ink = 0, borderInk = 0, coreExpected = 0, coreRecalled = 0;
              const rowInk = new Uint16Array(height);
              const pixels = imageData.data;
              const luminance = new Float32Array(width * height);
              for (let i = 0, j = 0; i < luminance.length; i++, j += 4) {
                luminance[i] = 0.2126 * pixels[j] + 0.7152 * pixels[j + 1] + 0.0722 * pixels[j + 2];
              }
              for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
                const i = y * width + x;
                if (result.alpha[i] > 0.12) {
                  ink++;
                  rowInk[y]++;
                  if (x < band || x >= width - band || y < band || y >= height - band) borderInk++;
                }
                if (luminance[i] >= 78) continue;
                let darkNeighbours = 0;
                for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
                  const xx = x + dx, yy = y + dy;
                  if (xx >= 0 && xx < width && yy >= 0 && yy < height
                      && luminance[yy * width + xx] < 100) darkNeighbours++;
                }
                if (darkNeighbours >= 9) {
                  coreExpected++;
                  if (result.alpha[i] > 0.12) coreRecalled++;
                }
              }

              const labels = new Uint8Array(width * height), components = [];
              for (let start = 0; start < labels.length; start++) {
                if (labels[start] || result.alpha[start] <= 0.35) continue;
                const queue = [start]; labels[start] = 1;
                let area = 0;
                for (let head = 0; head < queue.length; head++) {
                  const index = queue[head], x = index % width, y = (index / width) | 0;
                  area++;
                  for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
                    if (!dx && !dy) continue;
                    const xx = x + dx, yy = y + dy;
                    if (xx < 0 || xx >= width || yy < 0 || yy >= height) continue;
                    const next = yy * width + xx;
                    if (!labels[next] && result.alpha[next] > 0.35) {
                      labels[next] = 1;
                      queue.push(next);
                    }
                  }
                }
                components.push(area);
              }
              components.sort((a, b) => b - a);

              let normalizedInk = 0;
              for (const value of normalized) if (value > 0.12) normalizedInk++;
              const glyphUrl = buildGlyphs(normalized, 512).dark;
              source.style.cssText = 'width:420px;height:420px;object-fit:contain;background:#fff';
              const originalCard = document.createElement('section');
              originalCard.innerHTML = '<h2>Original</h2>';
              originalCard.appendChild(source);
              const outputCard = document.createElement('section');
              outputCard.innerHTML = `<h2>Extracted · ${result.mode}</h2>`
                + `<img src="${glyphUrl}" style="width:420px;height:420px;object-fit:contain;background:#fff">`;
              document.querySelector('#probe').append(originalCard, outputCard);

              const boxWidth = box.x1 >= box.x0 ? box.x1 - box.x0 + 1 : 0;
              const boxHeight = box.y1 >= box.y0 ? box.y1 - box.y0 + 1 : 0;
              return {
                mode: result.mode,
                width,
                height,
                box,
                boxWidth,
                boxHeight,
                ink,
                coverage: ink / (width * height),
                borderInk,
                borderRatio: ink ? borderInk / ink : 0,
                maxRowCoverage: boxWidth ? Math.max(...rowInk) / boxWidth : 0,
                coreExpected,
                coreRecalled,
                coreRecall: coreExpected ? coreRecalled / coreExpected : 0,
                components: components.slice(0, 5),
                normalizedInk,
                normalizedCoverage: normalizedInk / (512 * 512),
              };
            }
            """
        )
        page.screenshot(path=args.screenshot, full_page=True)

        # Reproduce the user's already-saved v17 failure: a textured square is stored
        # instead of the glyph. Reloading v18 must replace it from the embedded source
        # recovery payload, without asking the user to delete and import again.
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#fileInput").set_input_files(args.image)
        page.wait_for_function("library.size === 1", timeout=120_000)
        migration_case = page.evaluate(
            """
            async () => {
              const record = [...library.values()][0];
              const key = recoverKeyFor(record.name);
              const bad = document.createElement('canvas');
              bad.width = bad.height = 512;
              const context = bad.getContext('2d');
              context.fillStyle = '#444';
              context.fillRect(50, 50, 412, 412);
              record.dark = bad.toDataURL('image/png');
              record.v4 = 4;
              record.v5 = 17;
              await Store.put(record);
              return {id: record.id, key};
            }
            """
        )
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "library.size === 1 && [...library.values()][0].v5 === GLYPH_VERSION",
            timeout=120_000,
        )
        migration = page.evaluate(
            """
            ({id, key}) => {
              const record = library.get(id);
              return {
                key,
                version: record && record.v5,
                recovered: !!record && !!key && record.dark === RECOVER[key],
              };
            }
            """,
            migration_case,
        )
        browser.close()

    print(json.dumps({"page_errors": page_errors, "console_errors": console_errors,
                      "migration": migration, **metrics},
                     ensure_ascii=False, indent=2))
    assert not page_errors
    assert not console_errors
    assert metrics["mode"] == "dark", metrics
    assert 0.05 < metrics["coverage"] < 0.18, metrics
    assert metrics["boxWidth"] < metrics["width"] * 0.70, metrics
    assert metrics["boxHeight"] < metrics["height"] * 0.80, metrics
    assert metrics["borderRatio"] < 0.002, metrics
    assert metrics["maxRowCoverage"] < 0.75, metrics
    assert metrics["coreExpected"] > 20_000, metrics
    assert metrics["coreRecall"] > 0.92, metrics
    assert len(metrics["components"]) >= 2, metrics
    assert metrics["components"][0] > 40_000, metrics
    assert metrics["components"][1] > 25_000, metrics
    assert metrics["normalizedCoverage"] < 0.32, metrics
    assert migration["key"] == "微信图片_20260831134835_83_15", migration
    assert migration["version"] == 18, migration
    assert migration["recovered"], migration


if __name__ == "__main__":
    main()
