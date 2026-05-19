import asyncio
import re
from datetime import datetime
import requests

DEADLINE_DATE = "2026-07-12"
NTFY_TOPIC    = "Migri_Appointment"
MIGRI_URL     = "https://migri.vihta.com/public/migri/#/reservation"
DEADLINE      = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()


def notify(title, message, priority="default"):
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Click": MIGRI_URL,
            },
            timeout=10,
        )
        print(f"[ntfy] {title} -> {r.status_code}")
    except Exception as e:
        print(f"[ntfy] Error: {e}")


async def pick_option(page, ng_model, keyword):
    """Click a custom Angular dropdown by ng-model and pick option by keyword."""
    # Click the dropdown trigger div
    dropdown = page.locator(f"[ng-model='{ng_model}']")
    await dropdown.click()
    await page.wait_for_timeout(800)

    # Find all visible options inside this dropdown's listbox
    options = await page.locator("[role='option']").all()
    print(f"  Options visible for {ng_model}: {len(options)}")
    for opt in options:
        txt = (await opt.inner_text()).strip()
        print(f"    '{txt}'")
        if keyword.lower() in txt.lower():
            await opt.click()
            print(f"  Selected: '{txt}'")
            await page.wait_for_timeout(1500)
            return True

    print(f"  WARNING: No option matching '{keyword}' found")
    return False


async def get_slots():
    from playwright.async_api import async_playwright

    slots = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )

        print("Loading Migri...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        # Step 1: Select service category — "Oleskelulupa" (Residence permit)
        print("Step 1: Selecting service category...")
        await pick_option(page, "entitySelections.category.value", "Oleskelulupa")

        # Step 2: Select service — "5. Pysyva oleskelulupa" (Permanent residence)
        print("Step 2: Selecting service type...")
        await pick_option(page, "entitySelections.service.value", "5.")

        # Step 3: Select office — Helsinki
        # Office dropdown uses data-ng-model instead of ng-model
        print("Step 3: Selecting office...")
        office_dropdown = page.locator("[data-ng-model='entitySelections.locality.value']")
        if await office_dropdown.count() == 0:
            office_dropdown = page.locator("[ng-model='entitySelections.locality.value']")
        await office_dropdown.click()
        await page.wait_for_timeout(800)
        options = await page.locator("[role='option']").all()
        print(f"  Office options: {len(options)}")
        for opt in options:
            txt = (await opt.inner_text()).strip()
            if "helsinki" in txt.lower() or "malmi" in txt.lower():
                await opt.click()
                print(f"  Selected office: '{txt}'")
                await page.wait_for_timeout(1500)
                break

        # Step 4: Click "Hae vapaat ajat" (Search availability)
        print("Step 4: Clicking search...")
        search_btn = page.locator("button.ladda-button:not([disabled])")
        if await search_btn.count() > 0:
            await search_btn.first.click()
            print("Clicked search button")
        else:
            # Try by text
            btn = page.get_by_text("Hae vapaat ajat")
            await btn.first.click()
            print("Clicked by text")

        await page.wait_for_timeout(8000)

        # Step 5: Read results
        body = await page.inner_text("body")
        print(f"Page after search (first 1000):\n{body[:1000]}")

        # Look for available time slots
        # Migri shows times like "10:00" on clickable buttons after search
        available = await page.locator(
            ".reservation-time, [class*='reservation'], "
            "[class*='time-slot'], button[class*='available'], "
            "button[class*='time'], .available-time"
        ).all()
        print(f"Available slot elements: {len(available)}")

        for item in available:
            txt = (await item.inner_text()).strip()
            if txt:
                slots.append(txt)
                print(f"  Slot: '{txt}'")

        # Fallback: find dates and times in page text
        if not slots:
            dates = re.findall(r'\d{1,2}[.]\d{1,2}[.]\d{4}', body)
            times = re.findall(r'\b\d{1,2}:\d{2}\b', body)
            print(f"Fallback - Dates: {dates[:10]}, Times: {times[:10]}")
            for d in dates[:5]:
                slots.append(f"{d} - Helsinki service point (Malmi)")

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

    all_slots = await get_slots()
    print(f"Total slots found: {len(all_slots)}")

    early, later = [], []
    for raw in all_slots:
        d = parse_date(raw)
        if d and d <= DEADLINE:
            early.append((d, raw))
        elif d:
            later.append((d, raw))
        else:
            early.append((None, raw))

    early.sort(key=lambda x: x[0] or datetime.max.date())
    later.sort(key=lambda x: x[0] or datetime.max.date())

    if early:
        lines = "\n".join(f"- {raw}" for _, raw in early[:5])
        msg = (
            f"Found {len(early)} slot(s) before {DEADLINE_DATE}!\n\n"
            f"{lines}\n\n"
            f"Book now: migri.vihta.com\n"
            f"Checked: {checked_at}"
        )
        notify("MIGRI SLOT FOUND - Book now!", msg, priority="urgent")
        print(f"ALERT sent: {len(early)} early slots")

    elif later:
        d, raw = later[0]
        msg = (
            f"No slots before {DEADLINE_DATE}.\n"
            f"Earliest available: {raw}\n"
            f"Checked: {checked_at}"
        )
        notify("Migri checked - no early slots", msg, priority="low")
        print(f"No early slots. Earliest: {d}")

    else:
        msg = (
            f"No appointments visible on Migri right now.\n"
            f"Checked: {checked_at}"
        )
        notify("Migri checked - calendar empty", msg, priority="low")
        print("No slots found")


asyncio.run(main())
