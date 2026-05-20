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


async def get_all_slots():
    from playwright.async_api import async_playwright

    collected_slots = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        # ✅ intercept network responses
        async def handle_response(response):
            try:
                url = response.url

                # ✅ THIS is the key endpoint used by Migri
                if "GetAvailableTimes" in url or "timeslots" in url.lower():
                    data = await response.json()

                    # DEBUG (optional)
                    # print(data)

                    # Try to extract times
                    for entry in str(data).split(","):
                        m = re.search(r"\d{4}-\d{2}-\d{2}", entry)
                        t = re.search(r"\d{1,2}:\d{2}", entry)

                        if m and t:
                            dt = datetime.strptime(m.group(), "%Y-%m-%d").date()
                            collected_slots.append({
                                "date": dt,
                                "time": t.group(),
                                "office": "Helsinki (Malmi)"
                            })

            except Exception:
                pass

        page.on("response", handle_response)

        print("Loading Migri...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        print("Step 1...")
        await page.locator("[ng-model='entitySelections.category.value']").click()
        await page.get_by_role("option", name="Oleskelulupa").click()
        await page.wait_for_timeout(1500)

        print("Step 2...")
        await page.locator("[ng-model='entitySelections.service.value']").click()
        await page.get_by_role("option", name=re.compile("5[.]", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        print("Step 3...")
        await page.locator("[data-ng-model='entitySelections.locality.value']").click()
        await page.get_by_role("option", name=re.compile("Helsinki.*Malmi", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        print("Step 4: Searching...")
        await page.locator("[data-ng-click='searchDesktop()']").click()

        # ✅ Let all API calls happen
        await page.wait_for_timeout(15000)

        await browser.close()

    # ✅ deduplicate
    unique = list({
        (s['date'], s['time']): s
        for s in collected_slots
    }.values())

    return unique


async def main():
    print("Checking Migri...")

    try:
        all_slots = await get_all_slots()

        print(f"Total slots found: {len(all_slots)}")

        early = sorted(
            [s for s in all_slots if s['date'] <= DEADLINE],
            key=lambda s: (s['date'], s['time'])
        )

        if early:
            lines = "\n".join(
                f"- {s['date']} at {s['time']}"
                for s in early[:5]
            )

            telegram(f"MIGRI SLOT FOUND!\n\n{lines}")
            print("✅ ALERT sent")

        else:
            telegram("No early slots found")
            print("No early slots")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Error: {str(e)[:300]}")


asyncio.run(main())
``
