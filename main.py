#!/usr/bin/env python3
"""
Migri Appointment Checker
Uses a real browser to check Helsinki PR permit appointments.
Sends clean notifications via ntfy.sh
"""

import asyncio
import re
import time
from datetime import datetime
import requests

# YOUR SETTINGS
DEADLINE_DATE       = "2026-08-08"
NTFY_TOPIC          = "Migri_Appointment"
CHECK_INTERVAL_SECS = 900  # 15 minutes

MIGRI_URL = "https://migri.vihta.com/public/migri/#/reservation"
DEADLINE  = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify(title, message, priority="default"):
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": priority,
                "Click": MIGRI_URL,
            },
            timeout=10,
        )
        print(f"[ntfy] {title} -> {r.status_code}")
    except Exception as e:
        print(f"[ntfy] Error: {e}")


async def get_slots():
    from playwright.async_api import async_playwright

    slots = []  # list of dicts: {date, time, office}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        page = await browser.new_page(
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )

        try:
            print(f"[{now()}] Loading Migri...")
            await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)

            # Take screenshot to see current state
            await page.screenshot(path="/tmp/step0.png")
            print(f"[{now()}] Page loaded. Title: {await page.title()}")

            # Log all select elements and their options
            selects = await page.locator("select").all()
            print(f"[{now()}] Found {len(selects)} select elements")
            for i, sel in enumerate(selects):
                opts = await sel.locator("option").all()
                opt_texts = [await o.inner_text() for o in opts]
                print(f"  Select {i}: {opt_texts[:5]}")

            # Step 1: Select "Residence permit"
            for i, sel in enumerate(selects):
                opts = await sel.locator("option").all()
                for opt in opts:
                    txt = (await opt.inner_text()).strip()
                    if "residence" in txt.lower() and "permit" in txt.lower() and len(txt) < 30:
                        val = await opt.get_attribute("value")
                        await sel.select_option(value=val)
                        print(f"[{now()}] Step1: Selected '{txt}'")
                        await page.wait_for_timeout(2000)
                        break

            # Step 2: Select "Permanent residence permit"
            selects = await page.locator("select").all()
            for sel in selects:
                opts = await sel.locator("option").all()
                for opt in opts:
                    txt = (await opt.inner_text()).strip()
                    if "permanent" in txt.lower() or "5." in txt:
                        val = await opt.get_attribute("value")
                        await sel.select_option(value=val)
                        print(f"[{now()}] Step2: Selected '{txt}'")
                        await page.wait_for_timeout(2000)
                        break

            # Step 3: Select Helsinki office
            selects = await page.locator("select").all()
            for sel in selects:
                opts = await sel.locator("option").all()
                for opt in opts:
                    txt = (await opt.inner_text()).strip()
                    if "helsinki" in txt.lower() or "malmi" in txt.lower():
                        val = await opt.get_attribute("value")
                        await sel.select_option(value=val)
                        print(f"[{now()}] Step3: Selected '{txt}'")
                        await page.wait_for_timeout(1000)
                        break

            await page.screenshot(path="/tmp/step3.png")

            # Step 4: Click Search
            clicked = False
            for text in ["Search availability", "Search", "Hae", "Etsi"]:
                btn = page.get_by_role("button", name=re.compile(text, re.IGNORECASE))
                if await btn.count() > 0:
                    await btn.first.click()
                    print(f"[{now()}] Step4: Clicked '{text}' button")
                    clicked = True
                    break

            if not clicked:
                # Try any button
                btns = await page.locator("button").all()
                for btn in btns:
                    txt = (await btn.inner_text()).strip()
                    print(f"  Button: '{txt}'")
                if btns:
                    await btns[-1].click()
                    print(f"[{now()}] Step4: Clicked last button")

            await page.wait_for_timeout(7000)
            await page.screenshot(path="/tmp/step4.png")

            # Step 5: Scrape available dates and times from calendar
            print(f"[{now()}] Scraping calendar...")

            # Dump page text for debugging
            body_text = await page.inner_text("body")
            print(f"[{now()}] Page text snippet: {body_text[:500]}")

            # Look for date+time patterns in the page
            # Vihta shows times like "10:00" and dates like "12.6.2026" or "June 12"
            date_time_pattern = re.findall(
                r'(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2}|'
                r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})'
                r'.*?(\d{1,2}:\d{2})',
                body_text, re.IGNORECASE | re.DOTALL
            )

            # Also look for clickable calendar cells
            calendar_items = await page.locator(
                "button.available, td.available, [class*='available']:not([class*='unavailable']), "
                "[class*='slot']:not([disabled]), [class*='time-slot']:not([disabled])"
            ).all()

            print(f"[{now()}] Calendar items found: {len(calendar_items)}")

            for item in calendar_items:
                try:
                    txt = (await item.inner_text()).strip()
                    label = await item.get_attribute("aria-label") or ""
                    combined = label or txt
                    if combined:
                        slots.append({
                            "raw": combined,
                            "office": "Helsinki service point (Malmi)",
                        })
                        print(f"  Slot: {combined}")
                except Exception:
                    pass

            # If no structured slots, use text patterns
            if not slots and date_time_pattern:
                for date_str, time_str in date_time_pattern:
                    slots.append({
                        "raw": f"{date_str} at {time_str}",
                        "office": "Helsinki service point (Malmi)",
                    })

            # Last resort: find any time-like text near date text
            if not slots:
                times_found = re.findall(r'\b(\d{1,2}:\d{2})\b', body_text)
                dates_found = re.findall(r'\b(\d{1,2}[./]\d{1,2}[./]\d{4})\b', body_text)
                print(f"[{now()}] Dates in page: {dates_found[:5]}")
                print(f"[{now()}] Times in page: {times_found[:5]}")
                for d in dates_found[:5]:
                    for t in times_found[:2]:
                        slots.append({"raw": f"{d} at {t}", "office": "Helsinki service point (Malmi)"})

        except Exception as e:
            print(f"[{now()}] Error: {e}")
            import traceback
            traceback.print_exc()
            await page.screenshot(path="/tmp/error.png")

        await browser.close()

    return slots


def parse_date(raw):
    """Try to parse a date from raw slot text."""
    from dateutil import parser as dp
    try:
        return dp.parse(raw, dayfirst=True).date()
    except Exception:
        return None


def main():
    print("=" * 50)
    print("  Migri Checker (Browser mode)")
    print(f"  Deadline : {DEADLINE_DATE}")
    print(f"  Interval : {CHECK_INTERVAL_SECS // 60} min")
    print(f"  ntfy     : {NTFY_TOPIC}")
    print("=" * 50)

    notify(
        "Migri Checker started",
        f"Watching Helsinki PR appointments before {DEADLINE_DATE}. "
        f"Checking every {CHECK_INTERVAL_SECS // 60} min.",
        priority="low"
    )

    check_num = 0
    while True:
        check_num += 1
        checked_at = datetime.now().strftime("%d %b %Y at %H:%M")
        print(f"\n[{now()}] Check #{check_num}")

        try:
            all_slots = asyncio.run(get_slots())
            print(f"[{now()}] Total slots: {len(all_slots)}")

            early, later = [], []
            for s in all_slots:
                d = parse_date(s["raw"])
                if d:
                    if d <= DEADLINE:
                        early.append((d, s))
                    else:
                        later.append((d, s))
                else:
                    early.append((None, s))  # unknown date = include to be safe

            early.sort(key=lambda x: x[0] or datetime.max.date())
            later.sort(key=lambda x: x[0] or datetime.max.date())

            if early:
                lines = "\n".join(f"- {s['raw']} @ {s['office']}" for _, s in early[:5])
                msg = (
                    f"Found {len(early)} slot(s) before {DEADLINE_DATE}!\n\n"
                    f"{lines}\n\n"
                    f"Book now: migri.vihta.com\n"
                    f"Checked: {checked_at}"
                )
                notify("MIGRI SLOT FOUND - Book now!", msg, priority="urgent")
                print(f"[{now()}] ALERT sent! {len(early)} early slots")

            elif later:
                d, s = later[0]
                msg = (
                    f"No slots before {DEADLINE_DATE}.\n"
                    f"Earliest: {s['raw']} @ {s['office']}\n"
                    f"Checked: {checked_at}"
                )
                notify("Migri checked - no early slots", msg, priority="low")
                print(f"[{now()}] No early slots. Earliest after deadline: {d}")

            else:
                msg = (
                    f"No appointments visible on Migri.\n"
                    f"Calendar may be fully booked or new slots not released.\n"
                    f"Checked: {checked_at}"
                )
                notify("Migri checked - calendar empty", msg, priority="low")
                print(f"[{now()}] No slots found")

        except Exception as e:
            print(f"[{now()}] Unexpected error: {e}")
            notify("Migri checker error", f"Check #{check_num} failed: {str(e)[:200]}", priority="low")

        print(f"[{now()}] Sleeping {CHECK_INTERVAL_SECS // 60} min...")
        time.sleep(CHECK_INTERVAL_SECS)


if __name__ == "__main__":
    main()
