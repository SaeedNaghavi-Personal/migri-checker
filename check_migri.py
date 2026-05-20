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


async def extract_slots_from_text(page):
    """
    Robust extraction: bind each time to its closest date block.
    Works regardless of DOM structure.
    """
    text = await page.inner_text("body")

    slots = []

    # DEBUG (optional)
    # print(text[:2000])

    # Split into date blocks
    pattern = r'(\d{1,2}\.\d{2})\.\s*((?:.|\n)*?)(?=\d{1,2}\.\d{2}\.|$)'
    matches = re.findall(pattern, text)

    for date_str, block in matches:
        try:
            dt = datetime.strptime(f"{date_str}.{YEAR}", "%d.%m.%Y").date()
        except Exception:
            continue

        # Extract times inside this date block
        times = re.findall(r'\b\d{1,2}:\d{2}\b', block)

        for t in times:
            slots.append({
                'date': dt,
                'time': t,
                'office': 'Helsinki (Malmi)'
            })

    return slots


async def get_all_slots():
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

        # IMPORTANT: wait longer for Angular rendering
        await page.wait_for_timeout(8000)
        await page.wait_for_selector("body")

        for week_num in range(15):

            page_text = await page.inner_text("body")
            week_dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.', page_text)

            print(f"\nWeek {week_num+1}: {week_dates_raw[:7]}")

            # deadline check
            past_deadline = True
            for d in week_dates_raw[:7]:
                try:
                    dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
                    if dt <= DEADLINE:
                        past_deadline = False
                        break
                except:
                    pass

            # ✅ NEW extraction
            week_slots = await extract_slots_from_text(page)

            for s in week_slots:
                print(f" Found: {s['date']} {s['time']}")
                all_slots.append(s)

            if past_deadline and week_dates_raw:
                print("Reached past deadline, stopping.")
                break

            # next week
            await page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])").first.click()
            print("Clicked next week")
            await page.wait_for_timeout(3000)

        await browser.close()

    return all_slots


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
