import asyncio
import re
import os
from datetime import datetime
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


def parse_page_text(text):
    """
    Parse the Migri calendar page text.
    The format is:
      vk 29
      Toimipiste  Ma  10.08.  Ti  11.08.  Ke  12.08. ...
      Helsingin palvelupiste (Malmi)
      Kaupparaitti 10
      14:45  08:15  08:45  09:15 ...   <- times per column (day)

    We extract dates and match each time column to its date.
    """
    slots = []

    # Find the calendar section - starts with "Toimipiste"
    cal_start = text.find("Toimipiste")
    if cal_start == -1:
        print("No 'Toimipiste' found in page - search may not have returned results")
        return slots

    cal_text = text[cal_start:]
    print(f"Calendar section:\n{cal_text[:600]}")

    # Extract all dates like "10.08." in order
    dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.\s', cal_text)
    dates = []
    for d in dates_raw:
        try:
            dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
            if dt not in dates:
                dates.append(dt)
        except Exception:
            pass
    print(f"Dates found: {dates}")

    if not dates:
        return slots

    # Find the section after the office name/address
    # Office section: "Helsingin palvelupiste (Malmi)\nKaupparaitti 10\n00700 Helsinki\n"
    # Then times appear column by column
    office_match = re.search(r'Helsingin palvelupiste.*?\n.*?\n.*?\n', cal_text, re.DOTALL)
    if office_match:
        times_section = cal_text[office_match.end():]
    else:
        times_section = cal_text

    print(f"Times section:\n{times_section[:300]}")

    # Extract all time tokens
    all_times = re.findall(r'\b(\d{1,2}:\d{2})\b', times_section)
    print(f"All times: {all_times}")

    # The times appear left-to-right, top-to-bottom per column
    # From the previous log we saw each day had ~10 times
    # We group them by number of days
    n_days = len(dates)
    if n_days == 0:
        return slots

    # Split times into per-day chunks
    # Look for "Lisää" (More) markers which separate columns
    # Or split evenly
    chunks_raw = re.split(r'Lisää|Ei aikoja', times_section)
    print(f"Chunks: {len(chunks_raw)}")

    day_times = []
    for chunk in chunks_raw:
        times_in_chunk = re.findall(r'\b(\d{1,2}:\d{2})\b', chunk)
        if times_in_chunk:
            day_times.append(times_in_chunk)

    print(f"Day chunks: {day_times}")

    # Map each chunk to a date
    for i, (date, times) in enumerate(zip(dates, day_times)):
        for t in times:
            slots.append({'date': date, 'time': t, 'office': 'Helsinki (Malmi)'})
            print(f"  Slot: {date} {t}")

    # If chunks didn't work, fall back to even split
    if not slots and all_times:
        chunk_size = max(1, len(all_times) // n_days)
        for i, date in enumerate(dates):
            for t in all_times[i*chunk_size:(i+1)*chunk_size]:
                slots.append({'date': date, 'time': t, 'office': 'Helsinki (Malmi)'})
                print(f"  Even-split slot: {date} {t}")

    return slots


async def get_slots():
    from playwright.async_api import async_playwright

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
        await page.wait_for_timeout(8000)

        page_text = await page.inner_text("body")
        await browser.close()

    return parse_page_text(page_text)


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_slots()
        print(f"Total slots: {len(all_slots)}")

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
            print("No slots found")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Migri checker error:\n{str(e)[:300]}")


asyncio.run(main())
