import asyncio
import re
import os
from datetime import datetime
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT = os.environ["TELEGRAM_CHAT"]

DEADLINE_DATE = "2026-08-12"
MIGRI_URL = "https://migri.vihta.com/public/migri/#/reservation"

DEADLINE = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()
YEAR = datetime.now().year


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


async def extract_current_day_slots(page):
    """
    Extract slots for currently selected day
    (NO clicking, just read what's visible)
    """
    text = await page.inner_text("body")

    # ✅ Find selected date (format: Tue 11/08 or 11/08)
    m = re.search(r'\b(\d{1,2})/(\d{2})\b', text)
    if not m:
        print("⚠️ Could not detect selected date")
        return []

    day = int(m.group(1))
    month = int(m.group(2))

    dt = datetime(YEAR, month, day).date()

    times = re.findall(r'\b\d{1,2}:\d{2}\b', text)

    print(f" Selected day: {dt}, found {len(times)} times")

    return [
        {
            "date": dt,
            "time": t,
            "office": "Helsinki (Malmi)"
        }
        for t in times
    ]


async def get_all_slots():
    from playwright.async_api import async_playwright

    all_slots = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        page = await browser.new_page(
            locale="fi-FI",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        )

        print("Loading Migri...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(4000)

        print("Step 1: Residence permit...")
        await page.locator("[ng-model='entitySelections.category.value']").click()
        await page.get_by_role("option", name="Oleskelulupa").click()
        await page.wait_for_timeout(1500)

        print("Step 2: Permanent residence...")
        await page.locator("[ng-model='entitySelections.service.value']").click()
        await page.get_by_role("option", name=re.compile("5[.]", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        print("Step 3: Helsinki office...")
        await page.locator("[data-ng-model='entitySelections.locality.value']").click()
        await page.get_by_role("option", name=re.compile("Helsinki.*Malmi", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        print("Step 4: Searching...")
        await page.locator("[data-ng-click='searchDesktop()']").click()

        await page.wait_for_timeout(10000)

        for week_num in range(15):

            page_text = await page.inner_text("body")
            week_dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.', page_text)

            print(f"\nWeek {week_num + 1}: {week_dates_raw[:7]}")

            # ✅ Extract currently visible day ONLY
            slots = await extract_current_day_slots(page)
            all_slots.extend(slots)

            # Deadline check
            past_deadline = True
            for d in week_dates_raw[:7]:
                try:
                    dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
                    if dt <= DEADLINE:
                        past_deadline = False
                        break
                except:
                    pass

            if past_deadline and week_dates_raw:
                print("Reached past deadline, stopping.")
                break

            # Next week
            await page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])").first.click()
            print("Clicked next week")
            await page.wait_for_timeout(3000)

        await browser.close()

    # Remove duplicates
    unique = list({
        (s['date'], s['time']): s
        for s in all_slots
    }.values())

    return unique


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_all_slots()

        print(f"\nTotal slots: {len(all_slots)}")

        early = sorted(
            [s for s in all_slots if s['date'] <= DEADLINE],
            key=lambda s: (s['date'], s['time'])
        )

        later = sorted(
            [s for s in all_slots if s['date'] > DEADLINE],
            key=lambda s: (s['date'], s['time'])
        )

        print(f"Before deadline: {len(early)}, After: {len(later)}")

        if early:
            lines = "\n".join(
                f"- {s['date'].strftime('%a %d.%m.%Y')} at {s['time']}"
                for s in early[:5]
            )

            telegram(
                f"MIGRI SLOT FOUND!\n\n{len(early)} slot(s):\n{lines}"
            )
            print(f"✅ ALERT sent ({len(early)} early slots)")

        elif later:
            earliest = later[0]
            telegram(
                f"Earliest: {earliest['date']} {earliest['time']}"
            )
            print(f"No early slots. Earliest: {earliest['date']} {earliest['time']}")

        else:
            telegram("No appointments visible")
            print("❌ No slots found at all")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Error:\n{str(e)[:300]}")


asyncio.run(main())
