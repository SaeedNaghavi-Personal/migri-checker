#!/usr/bin/env python3
"""
Migri Appointment Checker
Checks Helsinki Migri office for Permanent Residence Permit slots.
Sends iPhone notifications via ntfy.sh
"""

import asyncio
import re
import sys
from datetime import datetime
from dateutil import parser as dateparser
import requests

# ─────────────────────────────────────────
# YOUR SETTINGS
# ─────────────────────────────────────────
DEADLINE_DATE        = "2026-07-12"      # alert only if slot is before this date
NTFY_TOPIC           = "Migri_Appointment"  # your ntfy topic name
CHECK_INTERVAL_SECS  = 900              # 900 = every 15 minutes
NOTIFY_EVERY_CHECK   = True            # True = send a status ping every check (so you know it's alive)
# ─────────────────────────────────────────

MIGRI_URL = "https://migri.vihta.com/public/migri/#/reservation"
DEADLINE  = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify(title, message, priority="default"):
    """Send push notification to iPhone via ntfy.sh"""
    priority_map = {"urgent": "urgent", "high": "high", "default": "default", "low": "low"}
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority_map.get(priority, "default"),
                "Tags": "calendar",
                "Click": MIGRI_URL,
            },
            timeout=10,
        )
        print(f"[ntfy] Sent: {title} (status {r.status_code})")
    except Exception as e:
        print(f"[ntfy] Error: {e}")


async def check_migri():
    """
    Opens Migri booking site in a real browser, navigates to the
    Helsinki PR appointment calendar, and returns all available dates.
    """
    from playwright.async_api import async_playwright

    slots = []
    page_html = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page(
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        try:
            print(f"[{now()}] Loading Migri page...")
            await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(4000)

            # ── Step 1: Pick service category ──
            # The page uses Angular/custom dropdowns — try clicking the first one
            print(f"[{now()}] Selecting service type...")
            
            # Try native select first
            selects = await page.locator("select").all()
            print(f"[{now()}] Found {len(selects)} select elements")

            if len(selects) >= 1:
                await selects[0].select_option(index=1)  # first real option
                await page.wait_for_timeout(1500)
                # Try to find "Residence permit" option
                opts = await selects[0].locator("option").all()
                for opt in opts:
                    txt = (await opt.inner_text()).strip()
                    if "residence" in txt.lower() or "oleskelulupa" in txt.lower():
                        await selects[0].select_option(label=txt)
                        print(f"[{now()}] Selected: {txt}")
                        await page.wait_for_timeout(2000)
                        break

            # ── Step 2: Pick sub-type (Permanent residence permit) ──
            selects = await page.locator("select").all()
            if len(selects) >= 2:
                opts = await selects[1].locator("option").all()
                for opt in opts:
                    txt = (await opt.inner_text()).strip()
                    if "permanent" in txt.lower() or "pysyvä" in txt.lower() or "5." in txt:
                        await selects[1].select_option(label=txt)
                        print(f"[{now()}] Selected sub-type: {txt}")
                        await page.wait_for_timeout(2000)
                        break

            # ── Step 3: Pick Helsinki location ──
            selects = await page.locator("select").all()
            for sel in selects:
                opts = await sel.locator("option").all()
                for opt in opts:
                    txt = (await opt.inner_text()).strip()
                    if "helsinki" in txt.lower() or "malmi" in txt.lower():
                        await sel.select_option(label=txt)
                        print(f"[{now()}] Selected location: {txt}")
                        await page.wait_for_timeout(1500)
                        break

            # ── Step 4: Click "Search availability" ──
            print(f"[{now()}] Clicking search...")
            # Try multiple ways to find the search button
            for selector in [
                "button:has-text('Search')",
                "button:has-text('Hae')",
                "button:has-text('availability')",
                "[type='submit']",
                "input[type='submit']",
            ]:
                btn = page.locator(selector)
                if await btn.count() > 0:
                    await btn.first.click()
                    print(f"[{now()}] Clicked: {selector}")
                    break

            await page.wait_for_timeout(6000)

            # ── Step 5: Grab the full page text and HTML ──
            page_html = await page.content()
            page_text = await page.inner_text("body")

            # ── Step 6: Find available dates ──
            # Method A: aria-label on calendar buttons/cells
            date_els = await page.locator(
                "[aria-label], td.available, td[class*='available'], "
                "button[class*='day']:not([disabled]), "
                ".day:not(.disabled):not(.grey):not(.other-month)"
            ).all()

            for el in date_els:
                try:
                    label = await el.get_attribute("aria-label") or ""
                    text  = (await el.inner_text()).strip()
                    raw   = label or text
                    if raw and len(raw) > 1:
                        try:
                            d = dateparser.parse(raw, dayfirst=True)
                            if d:
                                slots.append(d.date())
                        except Exception:
                            pass
                except Exception:
                    pass

            # Method B: regex scan of page text for date patterns
            if not slots:
                patterns = re.findall(
                    r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b"
                    r"|\b(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})\b",
                    page_text
                )
                for p_match in patterns:
                    raw = "".join(p_match)
                    try:
                        d = dateparser.parse(raw, dayfirst=True)
                        if d and 2024 <= d.year <= 2030:
                            slots.append(d.date())
                    except Exception:
                        pass

            print(f"[{now()}] Raw dates found: {slots}")

        except Exception as e:
            print(f"[{now()}] Page error: {e}")
            try:
                await page.screenshot(path="/tmp/migri_debug.png")
                print(f"[{now()}] Screenshot saved to /tmp/migri_debug.png")
            except Exception:
                pass

        await browser.close()

    return sorted(set(slots))


def format_date(d):
    """Format date nicely, e.g. Monday 15 June 2026"""
    return d.strftime("%A %d %B %Y")


async def main():
    print("=" * 50)
    print("  Migri Appointment Checker")
    print(f"  Deadline : {DEADLINE_DATE}")
    print(f"  Interval : {CHECK_INTERVAL_SECS // 60} minutes")
    print(f"  ntfy     : {NTFY_TOPIC}")
    print("=" * 50)

    # Startup notification
    notify(
        "Migri Checker is running",
        f"Watching Helsinki PR appointments before {DEADLINE_DATE}.\n"
        f"Checking every {CHECK_INTERVAL_SECS // 60} min. You will get an alert if a slot is found.",
        priority="default"
    )

    check_num = 0
    while True:
        check_num += 1
        print(f"\n[{now()}] ── Check #{check_num} ──")

        try:
            all_slots = await check_migri()

            # Filter slots before deadline
            early = [d for d in all_slots if d <= DEADLINE]
            all_future = [d for d in all_slots if d > datetime.now().date()]

            checked_at = datetime.now().strftime("%d %b %Y %H:%M")

            if early:
                # 🚨 URGENT: slots found before deadline
                earliest = min(early)
                slot_list = "\n".join(f"• {format_date(d)}" for d in sorted(early)[:5])
                msg = (
                    f"Earliest slot: {format_date(earliest)}\n"
                    f"\nAll slots before {DEADLINE_DATE}:\n{slot_list}\n"
                    f"\nChecked at: {checked_at}\n"
                    f"👉 Book now at migri.vihta.com"
                )
                print(f"[{now()}] 🚨 SLOTS FOUND: {early}")
                notify("🚨 Migri slot available! Book NOW", msg, priority="urgent")

            elif all_future:
                # Slots exist but all after deadline
                earliest = min(all_future)
                msg = (
                    f"No slots before {DEADLINE_DATE}.\n"
                    f"Earliest available: {format_date(earliest)}\n"
                    f"Checked at: {checked_at}"
                )
                print(f"[{now()}] No early slots. Earliest: {earliest}")
                if NOTIFY_EVERY_CHECK:
                    notify("Migri checked — no early slots", msg, priority="low")

            else:
                # No slots found at all (site may have no availability)
                msg = (
                    f"No appointments visible on Migri site.\n"
                    f"This could mean fully booked or a page load issue.\n"
                    f"Checked at: {checked_at}"
                )
                print(f"[{now()}] No slots found at all")
                if NOTIFY_EVERY_CHECK:
                    notify("Migri checked — no slots visible", msg, priority="low")

        except Exception as e:
            print(f"[{now()}] Unexpected error: {e}")
            notify("Migri checker error", f"Error at {now()}:\n{str(e)[:200]}", priority="low")

        print(f"[{now()}] Sleeping {CHECK_INTERVAL_SECS // 60} min...")
        await asyncio.sleep(CHECK_INTERVAL_SECS)


if __name__ == "__main__":
    asyncio.run(main())
