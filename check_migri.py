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


async def extract_slots_week(page, week_dates_raw):
    slots = []

    # ✅ find ALL clickable day cells in calendar row
    day_cells = page.locator("[ng-click*='selectDay'], [data-ng-click*='selectDay']")

    count = await day_cells.count()
    print(f"Found {count} clickable day cells")

    if count == 0:
        print("⚠️ No calendar day cells found")
        return slots

    for i in range(min(7, count)):
        try:
            date_str = week_dates_raw[i]
            dt = datetime.strptime(f"{date_str}.{YEAR}", "%d.%m.%Y").date()

            # ✅ click actual day cell (real Angular handler)
            await day_cells.nth(i).click()
            await page.wait_for_timeout(1500)

            print(f" Clicking day {date_str}")

            # ✅ click time-of-day tabs (important)
            for tab_name in ["Morning", "Day", "Afternoon", "Evening"]:
                try:
                    tab = page.get_by_text(tab_name)
                    if await tab.count() > 0:
                        await tab.first.click()
                        await page.wait_for_timeout(700)
                except:
                    pass

            # ✅ extract times from visible slots
            times = await page.evaluate("""
                () => {
                    const out = [];
                    const elements = document.querySelectorAll('*');

                    elements.forEach(el => {
                        const t = el.innerText?.trim();
                        if (t && /^\\d{1,2}:\\d{2}$/.test(t)) {
                            out.push(t);
                        }
                    });

                    return [...new Set(out)];
                }
            """)

            print(f"  → Found {len(times)} slots")

            for t in times:
                slots.append({
                    "date": dt,
                    "time": t,
                    "office": "Helsinki (Malmi)"
                })

        except Exception as e:
            print(f"Day error: {e}")

    return slots


async def get_all_slots():
    from playwright.async_api import async_playwright

    all_slots = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        page = await browser.new_page(
            locale="fi-FI",
            user_agent="Mozilla/5.0"
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
            week_dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})', page_text)

            print(f"\nWeek {week_num + 1}: {week_dates_raw[:7]}")

            week_slots = await extract_slots_week(page, week_dates_raw)
            all_slots.extend(week_slots)

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

            if past_deadline:
                print("Reached past deadline, stopping.")
                break

            await page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])").first.click()
            print("Clicked next week")
            await page.wait_for_timeout(3000)

        await browser.close()

    # ✅ deduplicate
    unique = list({
        (s['date'], s['time']): s for s in all_slots
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

        print(f"Before deadline: {len(early)}")

        if early:
            lines = "\n".join(
                f"- {s['date'].strftime('%a %d.%m.%Y')} at {s['time']}"
                for s in early[:5]
            )

            telegram(f"MIGRI SLOT FOUND!\n\n{lines}")
            print("✅ ALERT sent")

        else:
            telegram("No appointments found")
            print("❌ No slots found")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Error:\n{str(e)[:300]}")


asyncio.run(main())
