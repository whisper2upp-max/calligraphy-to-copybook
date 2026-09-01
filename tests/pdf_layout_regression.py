"""Verify fixed A4 grid edges and the downloadable current-layout PDF."""

from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


def close(actual: float, expected: float, tolerance: float = 0.08) -> bool:
    return abs(actual - expected) <= tolerance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("source_image")
    parser.add_argument("output_pdf")
    parser.add_argument("screenshot")
    args = parser.parse_args()

    output_pdf = Path(args.output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.unlink(missing_ok=True)
    page_errors: list[str] = []
    console_errors: list[str] = []
    layouts: dict[str, dict[str, object]] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1200}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.goto(args.base_url)
        page.wait_for_load_state("networkidle")
        page.locator("#fileInput").set_input_files(args.source_image)
        page.wait_for_function("sheet.length === 1 && document.querySelectorAll('#sheetChips .chip').length === 1")

        for cols, rows in [(5, 6), (3, 4), (7, 8), (2, 2), (1, 1), (1, 6), (5, 1), (12, 16)]:
            metrics = page.evaluate(
                """
                ([cols, rows]) => {
                  settings.cols = cols;
                  settings.rows = rows;
                  settings.repeat = 30;
                  settings.showTitle = false;
                  settings.mode = 'trace';
                  settings.grid = 'fang';
                  renderPages();
                  document.querySelector('#pagesScale').style.zoom = '1';
                  const model = pageModel();
                  const page = document.querySelector('.page');
                  const grid = page.querySelector('.grid');
                  const cells = [...grid.querySelectorAll('.cell')];
                  const pageRect = page.getBoundingClientRect();
                  const unit = pageRect.width / 210;
                  const relative = element => {
                    const rect = element.getBoundingClientRect();
                    return {
                      left: (rect.left - pageRect.left) / unit,
                      top: (rect.top - pageRect.top) / unit,
                      right: (rect.right - pageRect.left) / unit,
                      bottom: (rect.bottom - pageRect.top) / unit,
                      width: rect.width / unit,
                      height: rect.height / unit,
                    };
                  };
                  return {
                    frame: model.frame,
                    grid: relative(grid),
                    first: relative(cells[0]),
                    last: relative(cells[cells.length - 1]),
                    cellCount: cells.length,
                  };
                }
                """,
                [cols, rows],
            )
            key = f"{cols}x{rows}"
            layouts[key] = metrics
            frame = metrics["frame"]
            grid = metrics["grid"]
            first = metrics["first"]
            last = metrics["last"]
            assert metrics["cellCount"] == cols * rows, (key, metrics)
            assert close(grid["left"], 12) and close(grid["top"], 12), (key, metrics)
            assert close(grid["right"], 198) and close(grid["bottom"], 285), (key, metrics)
            assert close(first["left"], grid["left"]) and close(first["top"], grid["top"]), (key, metrics)
            assert close(last["right"], grid["right"]) and close(last["bottom"], grid["bottom"]), (key, metrics)
            if cols > 1 and rows > 1:
                assert close(frame["cellW"], frame["cellH"], 0.01), (key, metrics)
            if cols > 1:
                assert frame["gapX"] >= 2.99, (key, metrics)
            if rows > 1:
                assert frame["gapY"] >= 2.99, (key, metrics)

        page.evaluate(
            """
            () => {
              settings.cols = 3;
              settings.rows = 4;
              settings.repeat = 30;
              settings.showTitle = true;
              settings.title = '临摹字帖';
              settings.mode = 'trace';
              settings.grid = 'fang';
              renderSheet();
              renderPages();
            }
            """
        )
        with page.expect_download(timeout=180_000) as download_info:
            page.get_by_role("button", name="导出 PDF").click()
        download = download_info.value
        download.save_as(output_pdf)
        page.wait_for_function("!pdfExporting && !document.querySelector('#btnPdf').disabled", timeout=180_000)
        page.screenshot(path=args.screenshot, full_page=True)
        suggested_filename = download.suggested_filename
        context.close()
        browser.close()

    assert not page_errors, page_errors
    assert not console_errors, console_errors
    data = output_pdf.read_bytes()
    assert data.startswith(b"%PDF-1.4"), data[:20]
    assert data.rstrip().endswith(b"%%EOF"), data[-40:]
    page_count = len(re.findall(rb"/Type /Page\b", data))
    assert page_count == 3, page_count
    assert b"/Count 3" in data
    assert b"/MediaBox [0 0 595.276 841.890]" in data
    assert output_pdf.stat().st_size > 150_000, output_pdf.stat().st_size

    images: list[bytes] = []
    image_header = re.compile(rb"/Subtype /Image .*?/Length (\d+) >>\nstream\n")
    for match in image_header.finditer(data):
        length = int(match.group(1))
        start = match.end()
        images.append(data[start : start + length])
    assert len(images) == 3, len(images)
    page_image = Image.open(io.BytesIO(images[0])).convert("RGB")
    assert page_image.size == (2480, 3508), page_image.size

    scale = 300 / 25.4
    anchors = [
        (round(12 * scale), round(20 * scale)),
        (round(198 * scale), round(285 * scale)),
    ]
    for x, y in anchors:
        values = []
        for yy in range(max(0, y - 4), min(page_image.height, y + 5)):
            for xx in range(max(0, x - 4), min(page_image.width, x + 5)):
                r, g, b = page_image.getpixel((xx, yy))
                values.append((r + g + b) / 3)
        assert min(values) < 110, (x, y, min(values))

    # Current mode was trace: away from borders, the first cell must contain the gray glyph.
    cell_size = layouts["3x4"]["frame"]["cellW"]
    left = round((12 + cell_size * 0.15) * scale)
    top = round((20 + cell_size * 0.15) * scale)
    right = round((12 + cell_size * 0.85) * scale)
    bottom = round((20 + cell_size * 0.85) * scale)
    dark_inside = 0
    for y in range(top, bottom, 2):
        for x in range(left, right, 2):
            r, g, b = page_image.getpixel((x, y))
            if (r + g + b) / 3 < 235:
                dark_inside += 1
    assert dark_inside > 500, dark_inside

    title_pixels = 0
    for y in range(round(6 * scale), round(16 * scale), 2):
        for x in range(round(60 * scale), round(150 * scale), 2):
            r, g, b = page_image.getpixel((x, y))
            if (r + g + b) / 3 < 180:
                title_pixels += 1
    assert title_pixels > 100, title_pixels

    result = {
        "layouts": layouts,
        "pdf": {
            "path": str(output_pdf),
            "bytes": output_pdf.stat().st_size,
            "pages": page_count,
            "jpeg_pages": len(images),
            "page_pixels": page_image.size,
            "suggested_filename": suggested_filename,
            "trace_pixels_sampled": dark_inside,
            "title_pixels_sampled": title_pixels,
        },
        "page_errors": page_errors,
        "console_errors": console_errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
