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


async def get_week_slots(page):
    """
    Use JavaScript to read the DOM directly.
    Finds each day column header (date) and all time buttons under it.
    This is reliable because it reads the actual DOM structure, not text.
    """
    slots = await page.evaluate(f"""
        () => {{
            const slots = [];
            const year = {YEAR};

            // Find all day column headers — they contain dates like "10.08."
            // The calendar uses a grid where each column = one day
            const allEls = Array.from(document.querySelectorAll('*'));

            // Find elements whose text is exactly a date pattern like "10.08."
            const dateEls = allEls.filter(el => {{
                const t = el.innerText ? el.innerText.trim() : '';
                return /^\\d{{1,2}}\\.\\d{{2}}\\.$/.test(t) && el.children.length === 0;
            }});

            console.log('Date elements found:', dateEls.length);

            dateEls.forEach(dateEl => {{
                const dateText = dateEl.innerText.trim(); // e.g. "10.08."
                const dateParts = dateText.replace(/\\.$/, '').split('.');
                const day = parseInt(dateParts[0]);
                const month = parseInt(dateParts[1]);
                const fullDate = year + '-' + String(month).padStart(2,'0') + '-' + String(day).padStart(2,'0');

                // Walk up to find the column container
                // Try up to 6 levels up to find the column
                let col = dateEl;
                for (let i = 0; i < 6; i++) {{
                    col = col.parentElement;
                    if (!col) break;

                    // Find all time buttons in this column
                    const timeButtons = Array.from(col.querySelectorAll('button, a')).filter(btn => {{
                        const t = btn.innerText ? btn.innerText.trim() : '';
                        return /^\\d{{1,2}}:\\d{{2}}$/.test(t);
                    }});

                    if (timeButtons.length > 0) {{
                        // Make sure this column doesn't contain other date elements
                        // (to avoid grabbing the whole table)
                        const otherDates = Array.from(col.querySelectorAll('*')).filter(el => {{
                            const t = el.innerText ? el.innerText.trim() : '';
                            return /^\\d{{1,2}}\\.\\d{{2}}\\.$/.test(t) && el !== dateEl && el.children.length === 0;
                        }});

                        if (otherDates.length === 0) {{
                            // This is the right column — extract unique times
                            const times = [...new Set(timeButtons.map(b => b.innerText.trim()))];
                            times.forEach(t => slots.push({{date: fullDate, time: t}}));
                            console.log('Date:', fullDate, 'Times:', times);
                            break;
                        }}
                    }}
                }}
            }});

            return slots;
        }}
    """)
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
        await page.wait_for_timeout(6000)

        for week_num in range(15):
            # Get current week dates from page text
            page_text = await page.inner_text("body")
            week_dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.\s', page_text[page_text.find("Toimipiste"):])
            print(f"\nWeek {week_num+1}: {week_dates_raw[:7]}")

            # Check if past deadline
            past_deadline = True
            for d in week_dates_raw[:7]:
                try:
                    dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
                    if dt <= DEADLINE:
                        past_deadline = False
                        break
                except Exception:
                    pass

            # Read slots via JavaScript DOM
            week_slots = await get_week_slots(page)
            for s in week_slots:
                try:
                    dt = datetime.strptime(s['date'], "%Y-%m-%d").date()
                    print(f"  Found: {s['date']} {s['time']}")
                    all_slots.append({'date': dt, 'time': s['time'], 'office': 'Helsinki (Malmi)'})
                except Exception as e:
                    print(f"  Parse error: {e}")

            if past_deadline and week_dates_raw:
                print("Reached past deadline, stopping.")
                break

            # Next week
            await page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])").first.click()
            print("Clicked next week")
            await page.wait_for_timeout(2000)

        await browser.close()
    return all_slots


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_all_slots()
        print(f"\nTotal slots: {len(all_slots)}")

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
            print("No slots found at all")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Migri checker error:\n{str(e)[:300]}")


asyncio.run(main())
