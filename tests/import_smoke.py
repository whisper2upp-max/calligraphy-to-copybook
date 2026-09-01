import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rename",
        action="store_true",
        help="Rename the files in-browser so the embedded recovery library cannot mask pipeline defects.",
    )
    parser.add_argument(
        "--export-recovery",
        help="Write processed dark glyph data keyed by the original file stems.",
    )
    parser.add_argument("base_url")
    parser.add_argument("screenshot")
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()

    page_errors = []
    console_errors = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )

        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        grid_import_probe = page.evaluate(
            """
            () => {
              const S = 512;
              const canvas = document.createElement('canvas');
              canvas.width = canvas.height = S;
              const ctx = canvas.getContext('2d', {willReadFrequently: true});
              ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, S, S);
              ctx.strokeStyle = '#1f6f4a'; ctx.lineWidth = 4;
              ctx.beginPath(); ctx.moveTo(0, 256); ctx.lineTo(512, 256);
              ctx.moveTo(256, 0); ctx.lineTo(256, 512); ctx.stroke();
              /* 模拟原图中字在格线之上，斜笔穿过横竖中线。 */
              ctx.strokeStyle = '#b00020'; ctx.lineWidth = 18; ctx.lineCap = 'round';
              ctx.beginPath(); ctx.moveTo(90, 422); ctx.lineTo(422, 90); ctx.stroke();
              const result = analyze(ctx.getImageData(0, 0, S, S), S, S, 0.02, true);
              const at = (x, y) => result.alpha[y * S + x];
              return {
                crossing: at(256, 256),
                diagonalBefore: at(246, 266),
                diagonalAfter: at(266, 246),
                horizontalGridOnly: at(70, 256),
                verticalGridOnly: at(256, 70),
              };
            }
            """
        )
        color_ink_probe = page.evaluate(
            """
            () => {
              const S = 320;
              const trace = context => {
                context.beginPath();
                context.moveTo(58, 248);
                context.quadraticCurveTo(94, 168, 135, 62);
                context.moveTo(103, 178);
                context.bezierCurveTo(156, 134, 217, 121, 263, 90);
                context.moveTo(118, 205);
                context.quadraticCurveTo(191, 218, 254, 257);
                context.stroke();
                context.beginPath(); context.arc(94, 76, 10, 0, Math.PI * 2); context.fill();
              };
              const run = color => {
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = S;
                const context = canvas.getContext('2d', {willReadFrequently: true});
                const paper = context.createImageData(S, S);
                for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
                  const i = (y * S + x) * 4;
                  const texture = ((x * 37 + y * 53 + x * y * 3) % 39) - 19;
                  const shadow = Math.round(14 * (x + y) / (2 * S));
                  const value = Math.max(188, Math.min(250, 229 + texture - shadow));
                  paper.data[i] = paper.data[i + 1] = paper.data[i + 2] = value;
                  paper.data[i + 3] = 255;
                }
                context.putImageData(paper, 0, 0);
                context.strokeStyle = context.fillStyle = color;
                context.lineWidth = 18; context.lineCap = 'round'; context.lineJoin = 'round';
                trace(context);

                const coreCanvas = document.createElement('canvas');
                coreCanvas.width = coreCanvas.height = S;
                const core = coreCanvas.getContext('2d', {willReadFrequently: true});
                core.strokeStyle = core.fillStyle = '#fff'; core.lineWidth = 7;
                core.lineCap = 'round'; core.lineJoin = 'round'; trace(core);
                const corePixels = core.getImageData(0, 0, S, S).data;
                const supportCanvas = document.createElement('canvas');
                supportCanvas.width = supportCanvas.height = S;
                const support = supportCanvas.getContext('2d', {willReadFrequently: true});
                support.strokeStyle = support.fillStyle = '#fff'; support.lineWidth = 30;
                support.lineCap = 'round'; support.lineJoin = 'round'; trace(support);
                const supportPixels = support.getImageData(0, 0, S, S).data;

                const result = analyze(context.getImageData(0, 0, S, S), S, S, 0.004, true);
                let expected = 0, recalled = 0, outsideInk = 0, totalInk = 0;
                for (let i = 0; i < S * S; i++) {
                  if (corePixels[i * 4 + 3] > 128) {
                    expected++;
                    if (result.alpha[i] > 0.12) recalled++;
                  }
                  if (result.alpha[i] > 0.12) {
                    totalInk++;
                    if (supportPixels[i * 4 + 3] < 16) outsideInk++;
                  }
                }
                return {mode: result.mode, expected, recalled,
                  recall: expected ? recalled / expected : 0, outsideInk, totalInk,
                  outsideRatio: totalInk ? outsideInk / totalInk : 0};
              };
              return {
                red: run('#ff2038'),
                green: run('#00ff68'),
                blue: run('#0068ff'),
              };
            }
            """
        )
        cleanup_probe = page.evaluate(
            """
            () => {
              const S = 128;

              const frame = new Float32Array(S * S);
              for (let y = 24; y < S; y++) { frame[y * S] = 1; frame[y * S + S - 1] = 1; }
              for (let x = 0; x < S; x++) frame[(S - 1) * S + x] = 1;
              for (let y = 42; y <= 82; y++) for (let x = 46; x <= 74; x++) frame[y * S + x] = 1;
              removeFrameArtifacts(frame, S, S);
              let frameResidue = 0;
              for (let y = 24; y < S; y++) frameResidue += frame[y * S] + frame[y * S + S - 1];
              for (let x = 0; x < S; x++) frameResidue += frame[(S - 1) * S + x];

              /* 大量横纵散点能满足旧版“整行有墨”条件，但不是连续格线。 */
              const texture = new Float32Array(S * S);
              for (let x = 0; x < S; x += 3) texture[64 * S + x] = 1;
              for (let y = 0; y < S; y += 3) texture[y * S + 64] = 1;
              const textureBefore = Float32Array.from(texture);
              removeLines(texture, S, S, null);
              let textureChanged = 0;
              for (let i = 0; i < texture.length; i++) if (texture[i] !== textureBefore[i]) textureChanged++;

              const repaired = new Float32Array(S * S);
              for (let y = 42; y <= 84; y++) for (let x = 20; x <= 108; x++) repaired[y * S + x] = 1;
              /* 模拟粗墨边缘向内延伸的窄白裂口，以及完全包在粗墨里的小孔。 */
              for (let y = 42; y <= 62; y++) for (let x = 61; x <= 66; x++) repaired[y * S + x] = 0;
              for (let y = 66; y <= 70; y++) for (let x = 82; x <= 86; x++) repaired[y * S + x] = 0;
              repairInkVoids(repaired, S, S);

              const separated = new Float32Array(S * S);
              for (let y = 42; y <= 84; y++) {
                for (let x = 12; x <= 46; x++) separated[y * S + x] = 1;
                for (let x = 61; x <= 95; x++) separated[y * S + x] = 1;
              }
              repairInkVoids(separated, S, S);

              return {
                frameResidue,
                glyphCenter: frame[62 * S + 60],
                textureChanged,
                repairedSlit: repaired[55 * S + 63],
                repairedHole: repaired[68 * S + 84],
                preservedGap: separated[63 * S + 53],
              };
            }
            """
        )
        if args.rename:
            page.evaluate(
                """
                () => {
                  const input = document.createElement('input');
                  input.id = 'smokeInput';
                  input.type = 'file';
                  input.multiple = true;
                  document.body.appendChild(input);
                }
                """
            )
            page.locator("#smokeInput").set_input_files(args.images)
            page.evaluate(
                """
                async () => {
                  const files = [...document.querySelector('#smokeInput').files].map(
                    (file, index) => new File(
                      [file],
                      `回归-${String(index + 1).padStart(2, '0')}-${file.name}`,
                      {type: file.type, lastModified: file.lastModified},
                    )
                  );
                  await importFiles(files);
                }
                """,
            )
        else:
            page.locator("#fileInput").set_input_files(args.images)
        page.wait_for_function(
            f"document.querySelectorAll('#libGrid .lib-item').length === {len(args.images)}",
            timeout=240_000,
        )

        metrics = page.evaluate(
            """
            async () => {
              const result = [];
              for (const rec of library.values()) {
                const img = await loadImgEl(rec.dark);
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 512;
                const ctx = canvas.getContext('2d', {willReadFrequently: true});
                ctx.drawImage(img, 0, 0, 512, 512);
                const data = ctx.getImageData(0, 0, 512, 512).data;
                let x0 = 512, y0 = 512, x1 = -1, y1 = -1, ink = 0, border = 0;
                let alphaNonzero = 0, softAlpha = 0, opaqueAlpha = 0;
                let alphaMass = 0, halfAlpha = 0;
                const rowInk = new Uint16Array(512), colInk = new Uint16Array(512);
                const rowHalf = new Uint16Array(512), colHalf = new Uint16Array(512);
                const inkMask = new Uint8Array(512 * 512);
                for (let y = 0; y < 512; y++) {
                  for (let x = 0; x < 512; x++) {
                    const alpha = data[(y * 512 + x) * 4 + 3];
                    if (alpha > 0) alphaNonzero++;
                    alphaMass += alpha / 255;
                    if (alpha >= 128) { halfAlpha++; rowHalf[y]++; colHalf[x]++; }
                    if (alpha >= 10 && alpha <= 245) softAlpha++;
                    if (alpha === 255) opaqueAlpha++;
                    if (alpha > 30) {
                      inkMask[y * 512 + x] = 1;
                      ink++;
                      rowInk[y]++; colInk[x]++;
                      x0 = Math.min(x0, x); y0 = Math.min(y0, y);
                      x1 = Math.max(x1, x); y1 = Math.max(y1, y);
                      if (x === 0 || y === 0 || x === 511 || y === 511) border++;
                    }
                  }
                }
                let edgeTransitions = 0;
                for (let y = 0; y < 512; y++) for (let x = 0; x < 512; x++) {
                  const index = y * 512 + x;
                  if (x && inkMask[index] !== inkMask[index - 1]) edgeTransitions++;
                  if (y && inkMask[index] !== inkMask[index - 512]) edgeTransitions++;
                }
                result.push({
                  name: rec.name,
                  version: rec.v4,
                  processing_version: rec.v5,
                  bbox: [x0, y0, x1, y1],
                  width: x1 >= x0 ? x1 - x0 + 1 : 0,
                  height: y1 >= y0 ? y1 - y0 + 1 : 0,
                  ink,
                  border,
                  alpha_nonzero: alphaNonzero,
                  soft_alpha: softAlpha,
                  soft_alpha_ratio: alphaNonzero ? softAlpha / alphaNonzero : 0,
                  opaque_alpha_ratio: alphaNonzero ? opaqueAlpha / alphaNonzero : 0,
                  alpha_mass: alphaMass,
                  half_alpha: halfAlpha,
                  edge_transitions: edgeTransitions,
                  edge_transition_ratio: ink ? edgeTransitions / ink : 0,
                  max_row_coverage: x1 >= x0 ? Math.max(...rowInk) / (x1 - x0 + 1) : 0,
                  max_col_coverage: y1 >= y0 ? Math.max(...colInk) / (y1 - y0 + 1) : 0,
                  max_half_row_coverage: x1 >= x0 ? Math.max(...rowHalf) / (x1 - x0 + 1) : 0,
                  max_half_col_coverage: y1 >= y0 ? Math.max(...colHalf) / (y1 - y0 + 1) : 0,
                });
              }
              return result;
            }
            """
        )
        recovery_routes = page.evaluate(
            """
            () => {
              const longKey = Object.keys(RECOVER).find(key => key.length > 24);
              return {
                exact: recoverKeyFor('丑'),
                similarButDifferent: recoverKeyFor('丑陋'),
                truncatedLongName: longKey ? recoverKeyFor(longKey.slice(0, 24)) : null,
                longKey,
              };
            }
            """
        )
        recovery_images = page.evaluate(
            "() => [...library.values()].map(record => record.dark)"
        )
        layer_order = page.evaluate(
            """
            () => {
              const cell = document.querySelector('.cell');
              const glyph = cell && cell.querySelector('.glyph');
              const line = cell && cell.querySelector('.gl-h');
              return {
                glyph: glyph ? Number(getComputedStyle(glyph).zIndex) : -1,
                line: line ? Number(getComputedStyle(line).zIndex) : -1,
              };
            }
            """
        )
        page.screenshot(path=args.screenshot, full_page=True)
        forced_previous_case = page.evaluate(
            """
            async () => {
              const record = [...library.values()].find(item => recoverKeyFor(item.name));
              if (!record) return null;
              const key = recoverKeyFor(record.name);
              const canvas = document.createElement('canvas');
              canvas.width = canvas.height = 512;
              record.dark = canvas.toDataURL('image/png');
              record.v5 = 17;
              await Store.put(record);
              return {id: record.id, key};
            }
            """
        )
        forced_previous_recovered = None
        if forced_previous_case:
            page.reload()
            page.wait_for_load_state("networkidle")
            page.wait_for_function(
                "[...library.values()].every(record => record.v5 === GLYPH_VERSION)",
                timeout=120_000,
            )
            forced_previous_recovered = page.evaluate(
                """
                ({id, key}) => {
                  const record = library.get(id);
                  return !!record && record.dark === RECOVER[key] && record.v5 === GLYPH_VERSION;
                }
                """,
                forced_previous_case,
            )
        before_migration = page.evaluate(
            "() => [...library.values()].map(record => [record.id, record.dark])"
        )
        page.evaluate(
            """
            async () => {
              for (const record of library.values()) {
                delete record.v5;
                record.v4 = 4;
                await Store.put(record);
              }
            }
            """
        )
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            f"library.size === {len(args.images)} && "
            "[...library.values()].every(record => record.v5 === GLYPH_VERSION)",
            timeout=120_000,
        )
        after_migration = page.evaluate(
            "() => [...library.values()].map(record => [record.id, record.dark])"
        )
        browser.close()

    migration_preserved = dict(before_migration) == dict(after_migration)

    if args.export_recovery:
        assert args.rename, "Recovery export must bypass the existing recovery library."
        assert len(recovery_images) == len(args.images)
        recovery = {
            Path(image).stem: dark
            for image, dark in zip(args.images, recovery_images, strict=True)
        }
        Path(args.export_recovery).write_text(
            json.dumps(recovery, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "imported": len(metrics),
                "page_errors": page_errors,
                "console_errors": console_errors,
                "recovery_routes": recovery_routes,
                "grid_import_probe": grid_import_probe,
                "color_ink_probe": color_ink_probe,
                "cleanup_probe": cleanup_probe,
                "layer_order": layer_order,
                "forced_previous_recovered": forced_previous_recovered,
                "migration_preserved": migration_preserved,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    assert len(metrics) == len(args.images)
    assert not page_errors
    assert not console_errors
    assert all(metric["version"] == 4 for metric in metrics)
    assert all(metric["processing_version"] == 18 for metric in metrics)
    assert all(metric["width"] > 0 and metric["height"] > 0 for metric in metrics)
    assert all(metric["border"] == 0 for metric in metrics)
    assert recovery_routes["exact"] == "丑"
    assert recovery_routes["similarButDifferent"] is None
    assert recovery_routes["truncatedLongName"] == recovery_routes["longKey"]
    assert grid_import_probe["crossing"] > 0.5
    assert grid_import_probe["diagonalBefore"] > 0.5
    assert grid_import_probe["diagonalAfter"] > 0.5
    assert grid_import_probe["horizontalGridOnly"] < 0.1
    assert grid_import_probe["verticalGridOnly"] < 0.1
    for color, probe in color_ink_probe.items():
        assert probe["mode"] == "color", (color, probe)
        assert probe["recall"] >= 0.995, (color, probe)
        assert probe["outsideRatio"] < 0.02, (color, probe)
    assert cleanup_probe["frameResidue"] == 0
    assert cleanup_probe["glyphCenter"] > 0.5
    assert cleanup_probe["textureChanged"] == 0
    assert cleanup_probe["repairedSlit"] > 0.5
    assert cleanup_probe["repairedHole"] > 0.5
    assert cleanup_probe["preservedGap"] < 0.1
    assert layer_order["glyph"] > layer_order["line"]
    assert forced_previous_recovered in (None, True)
    assert migration_preserved
    if args.rename:
        target = next((metric for metric in metrics if metric["name"].endswith("否")), None)
        if target is not None:
            assert target["width"] >= 320, target
        framed = next((metric for metric in metrics if "ac81ab83" in metric["name"]), None)
        if framed is not None:
            assert framed["max_row_coverage"] < 0.9, framed
        for marker in (
            "cf3fdbd2e44c765b1d",
            "7eda7c9951d9e4e86e",
            "04acc911a9d497a871",
        ):
            colored = next((metric for metric in metrics if marker in metric["name"]), None)
            if colored is not None:
                assert colored["ink"] > 40_000, colored
                assert colored["width"] < 400, colored
                assert colored["max_row_coverage"] < 0.8, colored
        # Stored display names are truncated to 24 characters: both ...147 and ...148
        # collapse to the same prefix. Bind the metric by the original input order,
        # not by a lossy display name, so this gate cannot silently target the wrong file.
        green_index = next(
            (
                index
                for index, source_path in enumerate(args.images)
                if "20260829100147" in Path(source_path).stem
            ),
            None,
        )
        green_glyph = metrics[green_index] if green_index is not None else None
        if green_glyph is not None:
            # v11 把灰色凹凸纸纹一起当成墨迹，导致该字 66,524px、单行覆盖
            # 77.6%，肉眼呈现黑团；v12 虽消除黑团，但低色度细锋只召回
            # 83.9%。v13 补回细锋但重复硬化让轮廓变成黑色阶梯；v14
            # 恢复了抗锯齿，却仍把弱绿色边缘硬化成粗黑实体。v16 必须限制
            # 光学墨量和半透明以上的实体面积；edge/area 对细线天然更高，
            # 不能再用它作上限，否则测试会反向鼓励加粗。v17 只降低最终
            # 高斯强度提升清晰度，下面这些粗细/覆盖率门禁必须继续通过。
            assert 40_000 < green_glyph["ink"] < 46_000, green_glyph
            assert 26_000 < green_glyph["alpha_mass"] < 31_000, green_glyph
            assert 24_000 < green_glyph["half_alpha"] < 30_000, green_glyph
            assert green_glyph["max_row_coverage"] < 0.63, green_glyph
            assert green_glyph["max_half_row_coverage"] < 0.55, green_glyph
            assert green_glyph["soft_alpha_ratio"] > 0.65, green_glyph
            # 轻度抗锯齿会减少不可见软尾，使“不透明/非零”比例自然上升；
            # 半高核心面积和行宽才约束是否加粗。v17 实测 16.29%，留少量
            # 编码舍入余量，但仍远低于二值黑块阶段的 78.92%。
            assert green_glyph["opaque_alpha_ratio"] < 0.18, green_glyph


if __name__ == "__main__":
    main()
