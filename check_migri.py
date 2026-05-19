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

        selects = await page.locator("select").all()
        print(f"Found {len(selects)} select elements")
        for i, sel in enumerate(selects):
            opts = await sel.locator("option").all()
            texts = [(await o.inner_text()).strip() for o in opts]
            print(f"  Select {i}: {texts}")

        # Step 1: Residence permit
        for sel in await page.locator("select").all():
            for opt in await sel.locator("option").all():
                txt = (await opt.inner_text()).strip()
                if "residence" in txt.lower() and len(txt) < 30:
                    await sel.select_option(label=txt)
                    print(f"Step 1: '{txt}'")
                    await page.wait_for_timeout(2000)
                    break

        # Step 2: Permanent residence permit
        for sel in await page.locator("select").all():
            for opt in await sel.locator("option").all():
                txt = (await opt.inner_text()).strip()
                if "permanent" in txt.lower() or txt.startswith("5."):
                    await sel.select_option(label=txt)
                    print(f"Step 2: '{txt}'")
                    await page.wait_for_timeout(2000)
                    break

        # Step 3: Helsinki office
        for sel in await page.locator("select").all():
            for opt in await sel.locator("option").all():
                txt = (await opt.inner_text()).strip()
                if "helsinki" in txt.lower() or "malmi" in txt.lower():
                    await sel.select_option(label=txt)
                    print(f"Step 3: '{txt}'")
                    await page.wait_for_timeout(1000)
                    break

        # Step 4: Click search
        for text in ["Search availability", "Search", "Hae"]:
            btn = page.get_by_role("button", name=re.compile(text, re.IGNORECASE))
            if await btn.count() > 0:
                await btn.first.click()
                print(f"Step 4: Clicked '{text}'")
                break

        await page.wait_for_timeout(8000)

        body = await page.inner_text("body")
        print(f"Page text (first 800):\n{body[:800]}")

        # Find available slots
        for selector in [
            "button.available", "td.available",
            "[class*='available']:not([class*='un'])",
            "[class*='slot']:not([disabled])",
        ]:
            items = await page.locator(selector).all()
            if items:
                print(f"Found {len(items)} items: {selector}")
                for item in items:
                    txt = (await item.inner_text()).strip()
                    label = await item.get_attribute("aria-label") or ""
                    raw = label or txt
                    if raw and len(raw) > 1:
                        slots.append(raw)
                        print(f"  Slot: {raw}")
                break

        # Fallback: regex
        if not slots:
            dates = re.findall(r'\d{1,2}[.]\d{1,2}[.]\d{4}', body)
            times = re.findall(r'\b\d{1,2}:\d{2}\b', body)
            print(f"Dates: {dates}, Times: {times}")
            for d in dates:
                for t in times[:3]:
                    slots.append(f"{d} at {t}")

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
        print(f"No early slots. Earliest after deadline: {d}")

    else:
        msg = (
            f"No appointments visible on Migri right now.\n"
            f"Checked: {checked_at}"
        )
        notify("Migri checked - calendar empty", msg, priority="low")
        print("No slots found at all")


asyncio.run(main())
