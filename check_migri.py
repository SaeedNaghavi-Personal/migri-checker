import asyncio
import re
import os
from datetime import datetime, timedelta
import requests

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT"]
DEADLINE_DATE   = "2026-08-12"
MIGRI_URL       = "https://migri.vihta.com/public/migri/#/reservation"
DEADLINE        = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()
YEAR            = datetime.now().year


def telegram(message):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": message},
            timeout=10,
        )
        print(f"[telegram] status={r.status_code}")
    except Exception as e:
        print(f"[telegram] error: {e}")


def parse_week(text):
    """Parse one week's slots from page text. Returns list of {date, time} dicts."""
    slots = []
    cal_start = text.find("Toimipiste")
    if cal_start == -1:
        return slots

    cal_text = text[cal_start:]

    # Extract dates like "18.05." in order
    dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.\s', cal_text)
    dates = []
    for d in dates_raw:
        try:
            dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
            if dt not in dates:
                dates.append(dt)
        except Exception:
            pass

    if not dates:
        return slots

    # Split by "Ei aikoja" (no times) or find time chunks per day
    # Each day column either has times or "Ei aikoja"
    # Split the times section by day using "Ei aikoja" as marker for empty days
    office_end = cal_text.find("00700 Helsinki")
    if office_end == -1:
        office_end = cal_text.find("Kaupparaitti")
    if office_end == -1:
        office_end = cal_text.find("Helsingin palvelupiste")

    times_section = cal_text[office_end:] if office_end != -1 else cal_text

    # Split into per-day chunks using "Ei aikoja" as separator for empty days
    # and "Lisää" as separator between days with times
    day_chunks = re.split(r'Ei aikoja|Lisää\s*▼?', times_section)

    print(f"  Week dates: {dates}")
    print(f"  Day chunks count: {len(day_chunks)}")

    for i, (date, chunk) in enumerate(zip(dates, day_chunks)):
        times = re.findall(r'\b(\d{1,2}:\d{2})\b', chunk)
        for t in times:
            slots.append({'date': date, 'time': t, 'office': 'Helsinki (Malmi)'})
            print(f"  Found: {date} {t}")

    return slots


async def get_slots():
    from playwright.async_api import async_playwright

    all_slots = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="fi-FI",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )

        print("Loading Migri...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        print("Step 1: Residence permit...")
        await page.locator("[ng-model='entitySelections.category.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name="Oleskelulupa").click()
        await page.wait_for_timeout(1500)

        print("Step 2: Permanent residence...")
        await page.locator("[ng-model='entitySelections.service.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name=re.compile("5[.]", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        print("Step 3: Helsinki office...")
        await page.locator("[data-ng-model='entitySelections.locality.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name=re.compile("Helsinki.*Malmi", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        print("Step 4: Searching...")
        await page.locator("[data-ng-click='searchDesktop()']").click()
        await page.wait_for_timeout(6000)

        # Step 5: Navigate weeks until deadline
        today = datetime.now().date()
        max_weeks = 15  # scan up to 15 weeks ahead

        for week_num in range(max_weeks):
            page_text = await page.inner_text("body")

            # Check what week we're on
            week_dates = re.findall(r'\b(\d{1,2}\.\d{2})\.\s', page_text[page_text.find("Toimipiste"):])
            print(f"\nWeek {week_num+1}: dates found = {week_dates[:7]}")

            # Check if all dates in this week are past deadline
            past_deadline = True
            for d in week_dates:
                try:
                    dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
                    if dt <= DEADLINE:
                        past_deadline = False
                        break
                except Exception:
                    pass

            # Parse this week's slots
            week_slots = parse_week(page_text)
            all_slots.extend(week_slots)

            if past_deadline and week_dates:
                print(f"Reached past deadline, stopping.")
                break

            # Click next week button - use desktop version (not mobile)
            next_btn = page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])")
            if await next_btn.count() == 0:
                next_btn = page.locator("[data-ng-click='nextWeek()']").first
            await next_btn.click()
            print(f"Clicked next week")
            await page.wait_for_timeout(2000)

        await browser.close()
    return all_slots


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_slots()
        print(f"\nTotal slots found: {len(all_slots)}")

        early = sorted([s for s in all_slots if s['date'] <= DEADLINE], key=lambda s: (s['date'], s['time']))
        later = sorted([s for s in all_slots if s['date'] > DEADLINE], key=lambda s: (s['date'], s['time']))

        print(f"Before deadline: {len(early)}, After: {len(later)}")

        if early:
            lines = "\n".join(
                f"- {s['date'].strftime('%a %d.%m.%Y')} at {s['time']}"
                for s in early[:5]
            )
            msg = (
                f"MIGRI SLOT FOUND!\n\n"
                f"Helsinki (Malmi) - Permanent Residence\n\n"
                f"{len(early)} slot(s) before {DEADLINE_DATE}:\n"
                f"{lines}\n\n"
                f"Book now: migri.vihta.com\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print(f"ALERT: {len(early)} early slots!")

        elif later:
            earliest = later[0]
            msg = (
                f"Migri checked - no slots before {DEADLINE_DATE}\n\n"
                f"Earliest available:\n"
                f"{earliest['date'].strftime('%A %d.%m.%Y')} at {earliest['time']}\n"
                f"Office: Helsinki (Malmi)\n\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print(f"No early slots. Earliest: {earliest['date']} {earliest['time']}")

        else:
            telegram(f"Migri checked - no appointments visible\nChecked: {checked_at}")
            print("No slots found at all")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Migri checker error:\n{str(e)[:300]}")


asyncio.run(main())
