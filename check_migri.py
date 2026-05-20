import asyncio
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

        async def handle_response(response):
            try:
                url = response.url

                # ✅ DEBUG: print all API calls once
                if "vihta" in url:
                    print("API URL:", url)

                # ✅ TARGET: reservation API (this is the real one)
                if "reservation" in url.lower() and response.request.resource_type == "xhr":
                    try:
                        data = await response.json()
                    except:
                        return

                    # ✅ extract slots safely
                    def find_slots(obj):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k.lower() in ["date", "starttime", "time"]:
                                    print("DEBUG FIELD:", k, v)
                                find_slots(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                find_slots(item)

                    find_slots(data)

                    # ✅ generic extraction
                    import re
                    dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(data))
                    times = re.findall(r"\d{2}:\d{2}", str(data))

                    for d in dates:
                        for t in times:
                            try:
                                dt = datetime.strptime(d, "%Y-%m-%d").date()
                                collected_slots.append({
                                    "date": dt,
                                    "time": t
                                })
                            except:
                                pass

            except:
                pass

        page.on("response", handle_response)

        print("Loading Migri...")
        await page.goto(MIGRI_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(4000)

        print("Step 1...")
        await page.locator("[ng-model='entitySelections.category.value']").click()
        await page.get_by_role("option", name="Oleskelulupa").click()
        await page.wait_for_timeout(1500)

        print("Step 2...")
        await page.locator("[ng-model='entitySelections.service.value']").click()
        await page.get_by_role("option", name="5").click()
        await page.wait_for_timeout(1500)

        print("Step 3...")
        await page.locator("[data-ng-model='entitySelections.locality.value']").click()
        await page.get_by_role("option", name="Helsinki (Malmi)").click()
        await page.wait_for_timeout(1500)

        print("Step 4: Searching...")
        await page.locator("[data-ng-click='searchDesktop()']").click()

        # ✅ WAIT FOR API TRAFFIC
        await page.wait_for_timeout(20000)

        await browser.close()

    # ✅ deduplicate
    unique = list({
        (s['date'], s['time']): s
        for s in collected_slots
    }.values())

    return unique


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    all_slots = await get_all_slots()

    print(f"\nTotal slots: {len(all_slots)}")

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
        telegram("No slots found")
        print("❌ No slots found")


asyncio.run(main())
