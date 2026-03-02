"""Capture screenshots of the Shipwreck HTML report for README documentation.

Usage:
    uv run python scripts/generate_demo.py   # generate demo data first
    uv run python scripts/take_screenshots.py

Requires: playwright (uv add --dev playwright && uv run playwright install chromium)
"""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPORT = Path("demo_output/shipwreck.html")
OUT_DIR = Path("docs/screenshots")


def main() -> None:
    if not REPORT.exists():
        msg = f"Report not found at {REPORT}. Run 'uv run python scripts/generate_demo.py' first."
        raise FileNotFoundError(msg)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- Dark theme screenshots ---
        dark_ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            device_scale_factor=2,
            color_scheme="dark",
        )
        page = dark_ctx.new_page()
        page.goto(f"file://{REPORT.resolve()}")
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        # Enable edge labels for richer visuals
        page.click("#toggle-edge-labels")
        time.sleep(0.5)

        # Screenshot 1: Dark theme overview with edge labels
        page.screenshot(path=str(OUT_DIR / "graph-dark.png"))
        print(f"  Saved {OUT_DIR / 'graph-dark.png'}")

        # Screenshot 2: Dark theme — click a node to show detail panel
        node = page.locator("g.node").filter(has_text="backend-api").first
        node.click()
        time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "graph-dark-detail.png"))
        print(f"  Saved {OUT_DIR / 'graph-dark-detail.png'}")

        dark_ctx.close()

        # --- Light theme screenshots ---
        light_ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            device_scale_factor=2,
            color_scheme="light",
        )
        page = light_ctx.new_page()
        page.goto(f"file://{REPORT.resolve()}")
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        # Enable edge labels
        page.click("#toggle-edge-labels")
        time.sleep(0.5)

        # Screenshot 3: Light theme overview
        page.screenshot(path=str(OUT_DIR / "graph-light.png"))
        print(f"  Saved {OUT_DIR / 'graph-light.png'}")

        # Screenshot 4: Light theme with different node selected
        node = page.locator("g.node").filter(has_text="base-python").first
        node.click()
        time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "graph-light-detail.png"))
        print(f"  Saved {OUT_DIR / 'graph-light-detail.png'}")

        light_ctx.close()
        browser.close()

    print(f"\nAll screenshots saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
