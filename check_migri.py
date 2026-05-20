import asyncio
import re
from datetime import datetime
import requests

# ─────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────
TELEGRAM_TOKEN  = "8729890482:AAHH9BtKBUjYCLDMVKedclifWZ6mS8dTSvE"
TELEGRAM_CHAT   = "90616504"
DEADLINE_DATE   = "2026-08-08"  # change this to your deadline
MIGRI_URL       = "https://migri.vihta.com/public/migri/#/reservation"
# ─────────────────────────────────────────

DEADLINE = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()


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


async def get_slots():
    from playwright.async_api import async_playwright

    slots = []  # list of dicts: {date, time, office}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="fi-FI",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )

        print("Loading Migri...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        # Step 1: Select "Oleskelulupa" (Residence permit)
        print("Step 1: Residence permit...")
        await page.locator("[ng-model='entitySelections.category.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name="Oleskelulupa").click()
        await page.wait_for_timeout(1500)

        # Step 2: Select "5. Pysyvä oleskelulupa" (Permanent residence)
        print("Step 2: Permanent residence...")
        await page.locator("[ng-model='entitySelections.service.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name=re.compile("5\.", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        # Step 3: Select Helsinki office (Malmi)
        print("Step 3: Helsinki office...")
        await page.locator("[data-ng-model='entitySelections.locality.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name=re.compile("Helsinki.*Malmi", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        # Step 4: Click search (desktop button)
        print("Step 4: Searching...")
        await page.locator("[data-ng-click='searchDesktop()']").click()
        await page.wait_for_timeout(8000)

        # Step 5: Scrape results
        # Migri shows available times as clickable buttons with time text
        body = await page.inner_text("body")
        print(f"Page text after search:\n{body[:2000]}")

        # Find available time slot buttons
        time_buttons = await page.locator(
            "button[class*='btn-default'][data-ng-click*='select'], "
            "[class*='reservation-time'], "
            "[class*='available-time'], "
            "button[data-ng-click*='Time'], "
            "button[data-ng-click*='time']"
        ).all()

        print(f"Time buttons found: {len(time_buttons)}")
        for btn in time_buttons:
            txt = (await btn.inner_text()).strip()
            if txt and re.search(r'\d{1,2}:\d{2}', txt):
                slots.append(txt)
                print(f"  Slot: {txt}")

        # Fallback: look for date+time patterns in page text
        if not slots:
            # Find lines that look like appointment times
            lines = body.split('\n')
            for line in lines:
                line = line.strip()
                if re.search(r'\d{1,2}:\d{2}', line) and re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', line):
                    slots.append(f"{line} - Helsinki (Malmi)")
                    print(f"  Fallback slot: {line}")

        await browser.close()
    return slots


def parse_date(raw):
    from dateutil import parser as dp
    try:
        return dp.parse(raw, dayfirst=True).date()
    except Exception:
        return None


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    telegram(f"Migri checker started\nDeadline: {DEADLINE_DATE}\nChecked: {checked_at}")

    try:
        all_slots = await get_slots()
        print(f"Total slots found: {len(all_slots)}")

        # Filter by deadline
        early, later = [], []
        for raw in all_slots:
            d = parse_date(raw)
            if d and d <= DEADLINE:
                early.append((d, raw))
            elif d:
                later.append((d, raw))

        early.sort(key=lambda x: x[0])
        later.sort(key=lambda x: x[0])

        if early:
            lines = "\n".join(f"- {raw}" for _, raw in early[:5])
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
            d, raw = later[0]
            msg = (
                f"Migri checked - no slots before {DEADLINE_DATE}\n\n"
                f"Earliest available:\n{raw}\n\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print(f"No early slots. Earliest: {d}")

        else:
            msg = (
                f"Migri checked - calendar empty\n"
                f"No appointments visible right now.\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print("No slots found")

    except Exception as e:
        print(f"Error: {e}")
        telegram(f"Migri checker error:\n{str(e)[:300]}")


asyncio.run(main())
