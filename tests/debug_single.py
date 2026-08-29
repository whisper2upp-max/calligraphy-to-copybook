import argparse
import json

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("image")
    parser.add_argument("screenshot")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        page.evaluate(
            """
            () => {
              document.body.innerHTML = '<input id="debugInput" type="file">'
                + '<main id="debug" style="display:grid;grid-template-columns:repeat(4,320px);gap:20px;padding:20px;background:#eee"></main>';
            }
            """
        )
        page.locator("#debugInput").set_input_files(args.image)
        metrics = page.evaluate(
            """
            async () => {
              const file = document.querySelector('#debugInput').files[0];
              const img = await loadImageFile(file);
              const limit = 900;
              const k = Math.min(1, limit / Math.max(img.naturalWidth, img.naturalHeight));
              const w = Math.round(img.naturalWidth * k), h = Math.round(img.naturalHeight * k);
              const source = mkCanvas(w, h), ctx = source.getContext('2d', {willReadFrequently:true});
              ctx.drawImage(img, 0, 0, w, h);
              const data = ctx.getImageData(0, 0, w, h);
              const cases = [
                ['grid-r006', 0.006, true],
                ['no-grid-r006', 0.006, false],
                ['grid-r0001', 0.0001, true],
                ['no-grid-r0001', 0.0001, false],
              ];
              const main = document.querySelector('#debug');
              const sourceCard = document.createElement('section');
              sourceCard.innerHTML = '<h2>source</h2>';
              source.style.width = '300px'; source.style.height = 'auto';
              sourceCard.appendChild(source); main.appendChild(sourceCard);
              const out = [];
              for (const [label, ratio, removeGrid] of cases) {
                const result = analyze(data, w, h, ratio, removeGrid);
                const normalized = renormAlpha(result.alpha, 512, w, h);
                const glyph = buildGlyphs(normalized, 512).dark;
                const card = document.createElement('section');
                card.innerHTML = `<h2>${label} · ${result.mode}</h2><img style="width:300px;height:300px;object-fit:contain;background:#fff" src="${glyph}">`;
                main.appendChild(card);
                const box = inkBBox(result.alpha, w, h);
                let ink = 0;
                for (const alpha of result.alpha) if (alpha > 0.12) ink++;
                const labels = new Uint8Array(w * h), components = [];
                for (let start = 0; start < labels.length; start++) {
                  if (labels[start] || result.alpha[start] <= 0.35) continue;
                  const queue = [start]; labels[start] = 1;
                  let area = 0, x0 = w, y0 = h, x1 = -1, y1 = -1;
                  for (let head = 0; head < queue.length; head++) {
                    const index = queue[head], x = index % w, y = Math.floor(index / w);
                    area++; x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
                    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
                      if (!dx && !dy) continue;
                      const xx = x + dx, yy = y + dy;
                      if (xx < 0 || xx >= w || yy < 0 || yy >= h) continue;
                      const next = yy * w + xx;
                      if (!labels[next] && result.alpha[next] > 0.35) { labels[next] = 1; queue.push(next); }
                    }
                  }
                  components.push({area, box:[x0,y0,x1,y1]});
                }
                components.sort((a,b) => b.area - a.area);
                const holeLabels = new Uint8Array(512 * 512), holes = [];
                for (let start = 0; start < holeLabels.length; start++) {
                  if (holeLabels[start] || normalized[start] > 0.12) continue;
                  const queue = [start]; holeLabels[start] = 1;
                  let area = 0, x0 = 512, y0 = 512, x1 = -1, y1 = -1, touchesEdge = false;
                  for (let head = 0; head < queue.length; head++) {
                    const index = queue[head], x = index % 512, y = Math.floor(index / 512);
                    area++; x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
                    if (!x || x === 511 || !y || y === 511) touchesEdge = true;
                    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
                      if (!dx && !dy) continue;
                      const xx = x + dx, yy = y + dy;
                      if (xx < 0 || xx >= 512 || yy < 0 || yy >= 512) continue;
                      const next = yy * 512 + xx;
                      if (!holeLabels[next] && normalized[next] <= 0.12) { holeLabels[next] = 1; queue.push(next); }
                    }
                  }
                  if (!touchesEdge) holes.push({area, box:[x0,y0,x1,y1]});
                }
                holes.sort((a,b) => b.area - a.area);
                out.push({label, mode: result.mode, box, ink, components: components.slice(0, 20), holes: holes.slice(0, 40)});
              }
              return out;
            }
            """
        )
        page.screenshot(path=args.screenshot, full_page=True)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
