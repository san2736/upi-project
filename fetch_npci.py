#!/usr/bin/env python3
"""
fetch_npci.py - Scrape NPCI UPI monthly stats with a real browser.

Setup (once):
  pip install playwright pandas lxml
  playwright install chromium

Run:
  python fetch_npci.py            # opens a visible browser window
  python fetch_npci.py --debug    # also dumps each page state to debug_*.html
Output: npci.html (all captured tables), then feed it to collect_data.py:
  python collect_data.py --html npci.html --start 2019 --end 2026
"""
import argparse
import re
from playwright.sync_api import sync_playwright

URL = "https://www.npci.org.in/product/ecosystem-statistics/upi"
YEAR_RE = re.compile(r"^\s*(20\d{2})(\s*-\s*\d{2,4})?\s*$")  # "2024" or "2024-25"


def grab_tables(page):
    return page.eval_on_selector_all("table", "els => els.map(e => e.outerHTML)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)   # visible = fewer blocks
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(3000)

        # dismiss cookie banners if present
        for label in ["Accept", "Accept All", "I Agree", "OK"]:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click()
                break

        captured += grab_tables(page)
        if a.debug:
            open("debug_initial.html", "w").write(page.content())

        # find clickable year tabs / dropdown options and click each one
        candidates = page.locator("a, button, li, [role=tab], option, span")
        years = {}
        for i in range(candidates.count()):
            el = candidates.nth(i)
            try:
                txt = el.inner_text(timeout=500).strip()
            except Exception:
                continue
            if YEAR_RE.match(txt) and txt not in years:
                years[txt] = el
        print(f"Found year controls: {sorted(years)}")

        for yr, el in sorted(years.items()):
            try:
                if el.evaluate("e => e.tagName") == "OPTION":
                    sel = el.locator("xpath=..")
                    sel.select_option(label=yr)
                else:
                    el.scroll_into_view_if_needed()
                    el.click(timeout=3000)
                page.wait_for_timeout(2500)
                tables = grab_tables(page)
                captured += tables
                print(f"  {yr}: {len(tables)} table(s)")
                if a.debug:
                    open(f"debug_{yr}.html", "w").write(page.content())
            except Exception as e:
                print(f"  {yr}: click failed ({e})")

        browser.close()

    if not captured:
        print("No <table> elements found. Rerun with --debug and share "
              "debug_initial.html so the selector can be adapted.")
        return
    html = "<html><body>" + "\n".join(dict.fromkeys(captured)) + "</body></html>"
    open("npci.html", "w", encoding="utf-8").write(html)
    print(f"Saved {len(set(captured))} unique tables -> npci.html")


if __name__ == "__main__":
    main()
