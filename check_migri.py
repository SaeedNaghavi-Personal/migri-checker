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
    Directly extracts slots by locating columns/headers or tracking structural index alignment.
    This eliminates matching floating context dates outside the main timetable grid.
    """
    slots = await page.evaluate(f"""
        () => {{
            const slots = [];
            const year = {YEAR};

            // Locate the primary calendar wrapper element
            const calendarGrid = document.querySelector('.desktop-grid, [role="grid"], table');
            if (!calendarGrid) {{
                console.log('JS Error: Could not find calendar grid component on screen.');
                return slots;
            }}

            // Find columns or distinct day blocks inside the calendar grid
            const dayColumns = Array.from(calendarGrid.querySelectorAll('.day-column, th, td, .grid-col'));
            
            // If the layout uses a flat list of buttons with parent dates, inspect individual components safely
            const buttons = Array.from(calendarGrid.querySelectorAll('button, a'));
            const timeButtons = buttons.filter(b => /^\\d{{1,2}}:\\d{{2}}$/.test(b.textContent?.trim()));

            console.log('JS: Found ' + timeButtons.length + ' structural times inside the core grid.');

            timeButtons.forEach(btn => {{
                const timeText = btn.textContent.trim();
                
                // Let's attempt an intelligent structural lookup
                // First: Check if the button has an explicit aria-label containing the date (e.g., "Aika 11.08.2026 klo 09:00")
                const aria = btn.getAttribute('aria-label') || '';
                const ariaMatch = aria.match(/(\\d{{1,2}})\\.(\\d{{2}})\\./);
                if (ariaMatch) {{
                    const day = String(ariaMatch[1]).padStart(2, '0');
                    const month = String(ariaMatch[2]).padStart(2, '0');
                    slots.push({{date: `${{year}}-${{month}}-${{day}}`, time: timeText}});
                    return;
                }}

                // Second: Fallback to crawling strictly UP up to the parent day container inside the grid
                let parent = btn.parentElement;
                let foundDate = null;

                for (let i = 0; i < 7; i++) {{
                    if (!parent || parent === calendarGrid) break;
                    
                    // Look for structural text embedded within this specific grid block element
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

        # Track the signature markup of the last week to make sure our pagination clicks actually work
        last_page_fingerprint = ""

        for week_num in range(15):
            # Fallback text locator strictly focusing inside the main dynamic presentation node
            page_html = await page.locator("body").text_content()
            week_dates_raw = re.findall(r'\b(\d{1,2}\.\d{2})\.', page_html or "")
            week_dates_raw = list(dict.fromkeys(week_dates_raw)) # De-duplicate safely

            print(f"\n--- Week {week_num+1} Evaluation ---")
            print(f"Detected Dates raw context: {week_dates_raw[:7]}")

            # Read structural slots
            week_slots = await get_week_slots(page)
            
            # De-duplicate identical slots found on the same iteration frame
            unique_week_slots = []
            seen = set()
            for s in week_slots:
                key = (s['date'], s['time'])
                if key not in seen:
                    seen.add(key)
                    unique_week_slots.append(s)

            for s in unique_week_slots:
                try:
                    dt = datetime.strptime(s['date'], "%Y-%m-%d").date()
                    print(f"  Verified Slot -> Date: {s['date']} | Time: {s['time']}")
                    all_slots.append({'date': dt, 'time': s['time'], 'office': 'Helsinki (Malmi)'})
                except Exception as e:
                    print(f"  Parsing execution error: {e}")

            # Smart stopping check using parsed slot timestamps
            past_deadline = True
            if unique_week_slots:
                for s in unique_week_slots:
                    dt = datetime.strptime(s['date'], "%Y-%m-%d").date()
                    if dt <= DEADLINE:
                        past_deadline = False
                        break
            else:
                # If zero actual slots are found here, check via raw fallback dates text
                for d in week_dates_raw[:7]:
                    try:
                        dt = datetime.strptime(f"{d}.{YEAR}", "%d.%m.%Y").date()
                        if dt <= DEADLINE:
                            past_deadline = False
                            break
                    except Exception:
                        pass

            if past_deadline and (unique_week_slots or week_dates_raw):
                print("All slots or dates monitored in this frame are past deadline. Stopping search safely.")
                break

            # Capture current DOM signature state to verify page switching succeeded
            try:
                last_page_fingerprint = await page.locator("[role='grid'], table, .desktop-grid").inner_text()
            except Exception:
                last_page_fingerprint = ""

            # Navigate forward smoothly
            next_button = page.locator("[data-ng-click='nextWeek()']:not([id*='mobile'])").first
            await next_button.click()
            print("Clicked next week pagination element.")
            
            # CRITICAL FIX: Wait explicitly for the calendar text to change so we don't scan the same page twice
            for attempt in range(10):
                await page.wait_for_timeout(500)
                try:
                    current_fingerprint = await page.locator("[role='grid'], table, .desktop-grid").inner_text()
                    if current_fingerprint != last_page_fingerprint:
                        break
                except Exception:
                    pass

        await browser.close()
    return all_slots


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_all_slots()
        print(f"\nExecution summary -> Total items tracked: {len(all_slots)}")

        early = sorted([s for s in all_slots if s['date'] <= DEADLINE], key=lambda s: (s['date'], s['time']))
        later = sorted([s for s in all_slots if s['date'] > DEADLINE], key=lambda s: (s['date'], s['time']))

        print(f"Valid early matches: {len(early)} | Later scheduled matches: {len(later)}")

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
            print(f"ALERT: Sent telegram message with {len(early)} slots!")

        elif later:
            earliest = later[0]
            msg = (
                f"Migri checked - no slots before {DEADLINE_DATE}\n\n"
                f"Earliest available alternative:\n"
                f"{earliest['date'].strftime('%A %d.%m.%Y')} at {earliest['time']}\n"
                f"Office: Helsinki (Malmi)\n\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print(f"No early slots. Earliest found layout: {earliest['date']} {earliest['time']}")

        else:
            telegram(f"Migri checked - no appointments visible\nChecked: {checked_at}")
            print("No slots found at all across tracked parameters.")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Migri checker error:\n{str(e)[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
