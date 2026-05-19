#!/usr/bin/env python3
"""
Migri Appointment Checker
Checks the Migri booking site for available appointments and sends push notifications.

Requirements:
    pip install playwright requests python-dateutil
    playwright install chromium

Notification options (choose ONE and configure below):
  1. ntfy.sh  — free, open-source, no account needed (recommended)
  2. Pushover — free iOS/Android app, reliable push
"""

import asyncio
import json
import time
import sys
from datetime import datetime, date
from dateutil import parser as dateparser

# ──────────────────────────────────────────────
# CONFIGURATION — edit these values
# ──────────────────────────────────────────────

# How often to check (seconds). 600 = 10 minutes.
CHECK_INTERVAL_SECONDS = 600

# Deadline: only alert if a slot is BEFORE this date (YYYY-MM-DD)
DEADLINE_DATE = "2026-07-12"

# ── Notification method: "ntfy" or "pushover" ──
NOTIFY_METHOD = "ntfy"   # change to "pushover" if preferred

# ntfy.sh config (free, no account needed)
# 1. Install the ntfy app on your iPhone: https://apps.apple.com/app/ntfy/id1625396347
# 2. Subscribe to your unique topic name in the app (make it random/private!)
NTFY_TOPIC = "Migri_Appointment"  # <-- change this!

# Pushover config (if using Pushover instead)
PUSHOVER_USER_KEY = ""   # from pushover.net dashboard
PUSHOVER_APP_TOKEN = ""  # create an app at pushover.net

# ──────────────────────────────────────────────
# END CONFIG
# ──────────────────────────────────────────────

import requests

MIGRI_URL = "https://migri.vihta.com/public/migri/#/reservation"
DEADLINE = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()


def send_notification(title: str, message: str, url: str = MIGRI_URL):
    """Send a push notification via the configured method."""
    if NOTIFY_METHOD == "ntfy":
        try:
            requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": "urgent",
                    "Tags": "calendar,finland",
                    "Click": url,
                },
                timeout=10,
            )
            print(f"[ntfy] Notification sent: {title}")
        except Exception as e:
            print(f"[ntfy] Failed to send notification: {e}")

    elif NOTIFY_METHOD == "pushover":
        try:
            requests.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": PUSHOVER_APP_TOKEN,
                    "user": PUSHOVER_USER_KEY,
                    "title": title,
                    "message": message,
                    "url": url,
                    "url_title": "Book now on Migri",
                    "priority": 1,
                    "sound": "alien",
                },
                timeout=10,
            )
            print(f"[pushover] Notification sent: {title}")
        except Exception as e:
            print(f"[pushover] Failed to send notification: {e}")


async def check_appointments() -> list[str]:
    """
    Opens the Migri booking site with a real browser (Playwright/Chromium),
    navigates through the booking flow for:
      - Residence permit → 5. Permanent residence permit
      - 1 person
      - Helsinki service point
    and scrapes any available dates.
    """
    from playwright.async_api import async_playwright

    available_slots = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print(f"[{now()}] Opening Migri booking page...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        try:
            # Step 1: Select "Residence permit" from first dropdown
            print(f"[{now()}] Selecting 'Residence permit'...")
            first_dropdown = page.locator("select, vihta-select, [role='listbox']").first
            await first_dropdown.select_option(label="Residence permit")
            await page.wait_for_timeout(2000)

            # Step 2: Select "5. Permanent residence permit"
            print(f"[{now()}] Selecting 'Permanent residence permit'...")
            dropdowns = page.locator("select, vihta-select")
            await dropdowns.nth(1).select_option(label="5. Permanent residence permit")
            await page.wait_for_timeout(2000)

            # Step 3: Persons = 1 (should already be default)
            # Step 4: Location — Helsinki
            print(f"[{now()}] Selecting Helsinki service point...")
            location_dropdown = page.locator("select").filter(has_text="Helsinki")
            if await location_dropdown.count() == 0:
                # Try finding by index or aria
                all_selects = await page.locator("select").all()
                for sel in all_selects:
                    opts = await sel.inner_text()
                    if "Helsinki" in opts or "Malmi" in opts:
                        await sel.select_option(label="Helsinki : Helsinki service point (Malmi)")
                        break
            else:
                await location_dropdown.select_option(label="Helsinki : Helsinki service point (Malmi)")
            await page.wait_for_timeout(1000)

            # Step 5: Click "Search availability"
            print(f"[{now()}] Clicking 'Search availability'...")
            search_btn = page.get_by_role("button", name="Search availability")
            await search_btn.click()
            await page.wait_for_timeout(5000)

            # Step 6: Scrape available dates from the calendar
            print(f"[{now()}] Scraping available dates...")

            # Migri uses a calendar — look for enabled/clickable date cells
            date_elements = await page.locator(
                "td.available, td[class*='available'], button[class*='available'], "
                ".calendar-day:not(.disabled):not(.past), "
                "[aria-disabled='false'][aria-label]"
            ).all()

            for el in date_elements:
                try:
                    label = await el.get_attribute("aria-label") or await el.inner_text()
                    label = label.strip()
                    if label:
                        # Try to parse as a date
                        try:
                            slot_date = dateparser.parse(label, dayfirst=True).date()
                            available_slots.append(str(slot_date))
                        except Exception:
                            # Might be a time slot string
                            available_slots.append(label)
                except Exception:
                    pass

            # Fallback: grab any text that looks like a date
            if not available_slots:
                page_text = await page.inner_text("body")
                # Look for date patterns
                import re
                date_patterns = re.findall(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", page_text)
                available_slots = date_patterns

        except Exception as e:
            print(f"[{now()}] Error during page interaction: {e}")
            # Take a screenshot for debugging
            await page.screenshot(path="migri_debug.png")
            print("[debug] Screenshot saved to migri_debug.png")

        await browser.close()

    return available_slots


def filter_before_deadline(slots: list[str]) -> list[str]:
    """Keep only slots that are before the configured deadline."""
    filtered = []
    for slot in slots:
        try:
            slot_date = dateparser.parse(slot, dayfirst=True).date()
            if slot_date <= DEADLINE:
                filtered.append(slot)
        except Exception:
            # If we can't parse, include it anyway (better to over-notify)
            filtered.append(slot)
    return filtered


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def main():
    print("=" * 55)
    print("  Migri Appointment Checker")
    print(f"  Deadline: {DEADLINE_DATE}")
    print(f"  Check interval: {CHECK_INTERVAL_SECONDS // 60} minutes")
    print(f"  Notification: {NOTIFY_METHOD}")
    print("=" * 55)

    if NOTIFY_METHOD == "ntfy" and "YOUR-UNIQUE-TOPIC" in NTFY_TOPIC:
        print("\n⚠️  Please set your NTFY_TOPIC in the script before running!\n")
        sys.exit(1)

    # Send a startup test notification
    send_notification(
        "Migri Checker Started",
        f"Monitoring appointments before {DEADLINE_DATE}. "
        f"Checking every {CHECK_INTERVAL_SECONDS // 60} min.",
    )

    check_count = 0
    while True:
        check_count += 1
        print(f"\n[{now()}] Check #{check_count}")

        try:
            slots = await check_appointments()
            print(f"[{now()}] Raw slots found: {slots}")

            early_slots = filter_before_deadline(slots)

            if early_slots:
                msg = (
                    f"Found {len(early_slots)} slot(s) before {DEADLINE_DATE}!\n"
                    + "\n".join(f"  • {s}" for s in early_slots[:10])
                )
                print(f"\n🚨 SLOTS FOUND: {msg}\n")
                send_notification("🚨 Migri Slot Available!", msg)
            else:
                print(f"[{now()}] No slots before {DEADLINE_DATE}. Next check in {CHECK_INTERVAL_SECONDS // 60} min.")

        except Exception as e:
            print(f"[{now()}] Unexpected error: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
