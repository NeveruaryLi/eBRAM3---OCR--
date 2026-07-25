"""Playwright smoke test for the Case 6B V1.1 page."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8013/case6b")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        page.get_by_role("heading", name="服务协议草案生成").wait_for()
        page.locator("#templateInput").set_input_files(
            str(
                Path(__file__).resolve().parents[1]
                / "input_example"
                / "Usecase 6B"
                / "Input documents"
                / "UC6B_0_Format - Service Agreement.docx"
            )
        )
        assert page.locator("#startBtn").is_disabled()
        assert "DOCX" in page.locator("#selectedFiles").inner_text()
        page.screenshot(path=str(args.output / "case6b-v1.1-desktop.png"), full_page=True)
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(args.url)
        mobile.wait_for_load_state("networkidle")
        mobile.locator("#sidebarToggle").click()
        mobile.locator("#sidebar.open").wait_for()
        mobile.wait_for_timeout(350)
        mobile.screenshot(path=str(args.output / "case6b-v1.1-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    main()
