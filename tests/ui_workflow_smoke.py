"""Verify the 0.1.0 header docs, import placement, and print workflow."""

from __future__ import annotations

import argparse
import json

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("source_image")
    parser.add_argument("screenshot")
    args = parser.parse_args()

    page_errors: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1200})
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

        placement = page.evaluate(
            """
            () => ({
              headerButtons: document.querySelectorAll('#app > header button').length,
              usageInHeader: !!document.querySelector('#app > header .header-actions #btnUsage'),
              changelogInHeader: !!document.querySelector('#app > header .header-actions #btnChangelog'),
              importInLibrary: !!document.querySelector('.lib-card .card-title #btnImport'),
              printInPreview: !!document.querySelector('#previewSection > .card-title #btnPrint'),
              printButtons: [...document.querySelectorAll('button')]
                .filter(button => button.textContent.includes('打印')).length,
              sampleButton: !!document.querySelector('#btnSample'),
              sampleFunction: typeof window.makeSampleFiles,
              version: document.querySelector('.version-badge')?.textContent.trim(),
              oldDocsCard: !!document.querySelector('.docs-card'),
              usageDialog: !!document.querySelector('#usageDialog'),
              changelogDialog: !!document.querySelector('#changelogDialog'),
            })
            """
        )
        assert placement == {
            "headerButtons": 2,
            "usageInHeader": True,
            "changelogInHeader": True,
            "importInLibrary": True,
            "printInPreview": True,
            "printButtons": 1,
            "sampleButton": False,
            "sampleFunction": "undefined",
            "version": "v0.1.0",
            "oldDocsCard": False,
            "usageDialog": True,
            "changelogDialog": True,
        }, placement

        page.get_by_role("button", name="使用说明").click()
        assert page.locator("#usageDialog").evaluate("dialog => dialog.open")
        assert page.get_by_role("heading", name="使用说明").is_visible()
        page.locator("#usageDialog [data-close-dialog]").last.click()
        assert not page.locator("#usageDialog").evaluate("dialog => dialog.open")

        page.get_by_role("button", name="更新日志").click()
        assert page.locator("#changelogDialog").evaluate("dialog => dialog.open")
        assert page.get_by_role("heading", name="更新日志 v0.1.0").is_visible()
        page.locator("#changelogDialog [data-close-dialog]").last.click()
        assert not page.locator("#changelogDialog").evaluate("dialog => dialog.open")

        page.locator("#fileInput").set_input_files(args.source_image)
        page.wait_for_function("document.querySelectorAll('#sheetChips .chip').length === 1")
        page.evaluate(
            """
            () => {
              window.__printModes = [];
              window.print = () => window.__printModes.push(settings.mode);
            }
            """
        )

        labels = {
            "origin": "原图效果",
            "trace": "临摹描红",
            "blank": "空白格",
        }
        print_results: dict[str, dict[str, object]] = {}
        for mode, label in labels.items():
            page.locator("#setMode").select_option(mode)
            page.get_by_role("button", name="打印当前字帖").click()
            page.wait_for_timeout(100)
            print_results[mode] = page.evaluate(
                """
                ([expectedMode, expectedLabel]) => ({
                  setting: settings.mode,
                  selected: document.querySelector('#setMode').value,
                  previewMatches: document.querySelector('#previewInfo').textContent.includes(expectedLabel),
                  lastPrintMode: window.__printModes.at(-1),
                  printCount: window.__printModes.length,
                  expectedMode,
                })
                """,
                [mode, label],
            )
            result = print_results[mode]
            assert result["setting"] == mode, result
            assert result["selected"] == mode, result
            assert result["previewMatches"], result
            assert result["lastPrintMode"] == mode, result

        assert [print_results[mode]["printCount"] for mode in labels] == [1, 2, 3]
        page.locator("#setMode").select_option("origin")
        page.screenshot(path=args.screenshot, full_page=True)
        context.close()
        browser.close()

    assert not page_errors, page_errors
    assert not console_errors, console_errors
    print(
        json.dumps(
            {
                "placement": placement,
                "print_results": print_results,
                "page_errors": page_errors,
                "console_errors": console_errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
