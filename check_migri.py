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


async def click_dropdown_option(page, dropdown_index, keyword):
    """
    Click a custom Angular dropdown (not a native <select>),
    then pick the option containing keyword.
    """
    # Find all dropdown trigger elements
    dropdowns = await page.locator(
        "vihta-select, .dropdown, [class*='dropdown'], "
        "[data-ng-click*='open'], button[class*='select'], "
        "li[class*='select'], [role='combobox'], [role='listbox'], "
        ".btn-group, [class*='chosen']"
    ).all()

    print(f"Found {len(dropdowns)} dropdown-like elements")

    # Also try clicking by visible text on the page
    # The Migri page shows dropdowns as clickable divs/spans
    # Let's dump all clickable elements
    all_buttons = await page.locator("button, [ng-click], [data-ng-click]").all()
    print(f"Found {len(all_buttons)} buttons/clickable elements")
    for i, btn in enumerate(all_buttons[:20]):
        txt = (await btn.inner_text()).strip()
        cls = await btn.get_attribute("class") or ""
        print(f"  btn[{i}]: '{txt[:50]}' class='{cls[:50]}'")


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
        await page.wait_for_timeout(4000)

        # Dump the full page HTML structure to understand the dropdowns
        html = await page.content()
        # Save key parts
        print("=== PAGE TITLE ===")
        print(await page.title())

        print("=== ALL INPUT-LIKE ELEMENTS ===")
        for sel in ["select", "input", "[role='combobox']", "[role='listbox']",
                    "[role='option']", "vihta-select", ".ladda-button",
                    "[class*='select']", "[ng-model]", "[data-ng-model]"]:
            els = await page.locator(sel).all()
            if els:
                print(f"  '{sel}': {len(els)} found")
                for el in els[:3]:
                    txt = (await el.inner_text()).strip()[:60]
                    cls = (await el.get_attribute("class") or "")[:60]
                    print(f"    text='{txt}' class='{cls}'")

        print("=== VISIBLE TEXT ON PAGE ===")
        body = await page.inner_text("body")
        print(body[:1000])

        # Try interacting via JavaScript to fill the Angular form
        # First let's find the Angular scope and set values directly
        result = await page.evaluate("""
            () => {
                // Find all elements with Angular bindings
                const els = document.querySelectorAll('[ng-model], [data-ng-model], [ng-change], vihta-select');
                return Array.from(els).map(el => ({
                    tag: el.tagName,
                    ngModel: el.getAttribute('ng-model') || el.getAttribute('data-ng-model'),
                    ngChange: el.getAttribute('ng-change'),
                    class: el.className,
                    text: el.innerText ? el.innerText.substring(0, 50) : ''
                }));
            }
        """)
        print("=== ANGULAR ELEMENTS ===")
        for el in result:
            print(f"  {el}")

        # Try clicking the first dropdown (service category)
        # Migri uses custom vihta-select or similar components
        # Let's try clicking elements that look like dropdown triggers
        triggers = await page.locator(
            "[data-ng-click*='toggle'], [ng-click*='toggle'], "
            "[data-ng-click*='open'], [ng-click*='open'], "
            ".dropdown-toggle, [aria-haspopup='true'], "
            "[aria-expanded], button:not([disabled])"
        ).all()

        print(f"\n=== DROPDOWN TRIGGERS: {len(triggers)} ===")
        for i, t in enumerate(triggers[:10]):
            txt = (await t.inner_text()).strip()[:60]
            cls = (await t.get_attribute("class") or "")[:40]
            print(f"  [{i}] '{txt}' class='{cls}'")

        # Step 1: Click the first dropdown trigger
        if triggers:
            await triggers[0].click()
            await page.wait_for_timeout(1500)
            print("Clicked first trigger")

            # Now find options that appeared
            options = await page.locator(
                "li[role='option'], [role='option'], .dropdown-item, "
                "li a, ul li, [class*='option']"
            ).all()
            print(f"Options visible: {len(options)}")
            for opt in options[:10]:
                txt = (await opt.inner_text()).strip()
                print(f"  option: '{txt}'")
                if "residence" in txt.lower() and len(txt) < 40:
                    await opt.click()
                    print(f"Selected: '{txt}'")
                    await page.wait_for_timeout(2000)
                    break

        # Step 2: Second dropdown - permanent residence
        triggers2 = await page.locator(
            "[data-ng-click*='toggle'], [ng-click*='toggle'], "
            ".dropdown-toggle, [aria-haspopup='true'], button:not([disabled])"
        ).all()

        for t in triggers2:
            txt = (await t.inner_text()).strip()
            if "permanent" in txt.lower() or "select" in txt.lower() or txt == "":
                await t.click()
                await page.wait_for_timeout(1000)
                options2 = await page.locator("[role='option'], .dropdown-item, li a").all()
                for opt in options2:
                    otxt = (await opt.inner_text()).strip()
                    if "permanent" in otxt.lower() or "5." in otxt:
                        await opt.click()
                        print(f"Selected sub-type: '{otxt}'")
                        await page.wait_for_timeout(2000)
                        break
                break

        # Step 3: Helsinki office
        triggers3 = await page.locator(
            "[data-ng-click*='toggle'], .dropdown-toggle, button:not([disabled])"
        ).all()
        for t in triggers3:
            txt = (await t.inner_text()).strip()
            if "helsinki" in txt.lower() or "office" in txt.lower() or "location" in txt.lower():
                await t.click()
                await page.wait_for_timeout(1000)
                options3 = await page.locator("[role='option'], .dropdown-item, li a").all()
                for opt in options3:
                    otxt = (await opt.inner_text()).strip()
                    if "helsinki" in otxt.lower() or "malmi" in otxt.lower():
                        await opt.click()
                        print(f"Selected office: '{otxt}'")
                        await page.wait_for_timeout(1000)
                        break
                break

        # Step 4: Click search - find any enabled button
        search_btn = page.locator("button:not([disabled])[class*='btn-primary'], button:not([disabled])[class*='search']")
        if await search_btn.count() > 0:
            await search_btn.first.click()
            print("Clicked search button")
            await page.wait_for_timeout(8000)

        # Final: dump page state
        body2 = await page.inner_text("body")
        print(f"\n=== PAGE AFTER SEARCH (first 1000) ===\n{body2[:1000]}")

        # Look for time slots
        dates = re.findall(r'\d{1,2}[.]\d{1,2}[.]\d{4}', body2)
        times = re.findall(r'\b\d{1,2}:\d{2}\b', body2)
        print(f"Dates: {dates[:10]}")
        print(f"Times: {times[:10]}")

        for d in dates[:5]:
            for t in times[:3]:
                slots.append(f"{d} klo {t} - Helsinki service point (Malmi)")

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
