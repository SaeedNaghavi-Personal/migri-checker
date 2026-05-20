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
    Finds available time slots cleanly.
    Locates time buttons first, then climbs up the DOM to extract the matching date.
    Uses textContent instead of innerText to bypass headless visibility restrictions.
    """
    slots = await page.evaluate(f"""
        () => {{
            const slots = [];
            const year = {YEAR};

            // 1. Find all potential elements on the page
            const buttons = Array.from(document.querySelectorAll('button, a, .time-slot'));
            
            // 2. Filter for elements containing exactly a time pattern like "09:15" or "14:30"
            const timeButtons = buttons.filter(b => {{
                const t = b.textContent ? b.textContent.trim() : '';
                return /^\\d{{1,2}}:\\d{{2}}$/.test(t);
            }});

            console.log('JS: Found ' + timeButtons.length + ' raw time elements on this page.');

            // 3. For each time slot button, crawl upwards to find its calendar date context
            timeButtons.forEach(btn => {{
                const timeText = btn.textContent.trim();
                let parent = btn.parentElement;
                let foundDate = null;

                // Traverse up up to 10 parent levels to scan for a day/date indicator
                for (let i = 0; i < 10; i++) {{
                    if (!parent || parent === document.body) break;

                    // Search the text inside this container for standard Finnish date headers like "20.05."
                    const text = parent.textContent || "";
                    const dateMatch = text.match(/\\b(\\d{{1,2}})\\.(\\d{{2}})\\./);

                    if (dateMatch) {{
                        const day = String(dateMatch[1]).padStart(2, '0');
                        const month = String(dateMatch[2]).padStart(2, '0');
                        foundDate = year + '-' + month + '-' + day;
                        break;
                    }}
                    parent = parent.parentElement;
                }}

                if (foundDate) {{
                    slots.push({{date: foundDate, time: timeText}});
                }}
            }});

            console.log('JS: Successfully mapped ' + slots.length + ' slots to dates.');
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

        # Pipe browser console logs directly into your Python print/GitHub Actions logs
        page.on("console", lambda msg: print(f"[Browser Console] {msg.text}"))

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
            # Get current week dates from page text using text_content()
            page_text = await page.content()
            # Dynamic strip down to relevant areas to extract visible dates
            week_dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.\s', page_text)
            # Remove duplicates while preserving structural order
            week_dates_raw = list(dict.fromkeys(week_dates_raw))
            
            print(f"\nWeek {week_num+1} Headers Extracted: {week_dates_raw[:7]}")

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

            # Read slots via updated JavaScript logic
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

            # Next week navigation
            await page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])").first.click()
            print("Clicked next week")
            await page.wait_for_timeout(2500)

        await browser.close()
    return all_slots


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_all_slots()
        print(f"\nTotal slots found across all weeks: {len(all_slots)}")

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


if __name__ == "__main__":
    asyncio.run(main())
