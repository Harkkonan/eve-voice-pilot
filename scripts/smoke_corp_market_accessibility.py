from __future__ import annotations

import argparse
import sys
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def load_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is not installed. Install it in a dev environment with "
            "`python -m pip install playwright` and `python -m playwright install chromium`."
        ) from exc
    return sync_playwright


def check_headers(response: Any, url: str) -> None:
    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    for required in ("content-security-policy", "x-content-type-options", "x-frame-options"):
        if required not in headers:
            fail(f"{url} missing {required} response header")
    if url.startswith("https://") and "strict-transport-security" not in headers:
        fail(f"{url} missing strict-transport-security response header")


def check_viewport(page: Any, *, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(page.url, wait_until="networkidle")
    overflow = page.evaluate(
        """
        () => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
          bodyText: document.body ? document.body.innerText.slice(0, 200) : ""
        })
        """
    )
    if overflow["scrollWidth"] > overflow["clientWidth"] + 2:
        fail(f"horizontal overflow at {width}x{height}: {overflow}")
    if "Corp Market Concierge" not in overflow["bodyText"] and "Flight Attendant" not in overflow["bodyText"]:
        fail(f"dashboard text did not render at {width}x{height}")


def check_controls(page: Any) -> None:
    unnamed = page.locator(
        "button:not([aria-label]):not([title]), "
        "input:not([type=hidden]):not([aria-label]):not([title]), "
        "select:not([aria-label]):not([title]), "
        "textarea:not([aria-label]):not([title])"
    ).evaluate_all(
        """
        nodes => nodes
          .filter(node => {
            const style = window.getComputedStyle(node);
            const visible = style.visibility !== "hidden"
              && style.display !== "none"
              && node.getClientRects().length > 0;
            if (!visible) return false;
            const id = node.id || "";
            const hasLabel = id && document.querySelector(`label[for="${CSS.escape(id)}"]`);
            const text = (node.innerText || node.value || "").trim();
            return !hasLabel && !text;
          })
          .slice(0, 20)
          .map(node => node.outerHTML.slice(0, 200))
        """
    )
    if unnamed:
        fail("visible controls without accessible names: " + "; ".join(unnamed))

    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement && document.activeElement.tagName")
    if not focused or focused == "BODY":
        fail("keyboard Tab did not move focus to an interactive element")


def check_mining_yield_tab(page: Any) -> None:
    button = page.locator('[data-tab-target="mining-yield"]')
    if button.count() != 1:
        fail("Mining Yield tab is missing from the rendered dashboard")
    button.click()
    panel = page.locator("#tab-mining-yield")
    panel.wait_for(state="visible", timeout=5000)
    panel_text = panel.inner_text()
    for expected in (
        "Mining Yield",
        "Manual session hours",
        "Refresh Mining Ledger",
        "Opt In To Mining Ledger",
        "Mining Ledger Output",
        "Copy CSV",
        "Download CSV",
    ):
        if expected not in panel_text:
            fail(f"Mining Yield tab missing visible text: {expected}")
    if not page.locator("#mining-yield-refresh").is_visible():
        fail("Mining Yield refresh button is not visible")
    if not page.locator("#mining-yield-copy-csv").is_visible():
        fail("Mining Yield CSV copy button is not visible")


def run(url: str) -> None:
    sync_playwright = load_playwright()
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        response = page.goto(url, wait_until="networkidle")
        if response is None or not response.ok:
            status = response.status if response is not None else "no response"
            fail(f"{url} returned {status}")
        check_headers(response, url)
        check_mining_yield_tab(page)
        check_controls(page)
        check_viewport(page, width=1440, height=1000)
        check_viewport(page, width=390, height=844)
        browser.close()
    if console_errors:
        fail("console errors: " + "; ".join(console_errors[:10]))
    print(f"Corp Market browser/accessibility smoke passed for {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser/accessibility smoke check for Corp Market / Flight Attendant.")
    parser.add_argument("url", help="Base URL such as http://127.0.0.1:8770 or the public HTTPS URL.")
    args = parser.parse_args()
    run(args.url.rstrip("/") + "/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
