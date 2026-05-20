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

        # Step 1: Residence permit
        print("Step 1: Residence permit...")
        await page.locator("[ng-model='entitySelections.category.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name="Oleskelulupa").click()
        await page.wait_for_timeout(1500)

        # Step 2: Permanent residence
        print("Step 2: Permanent residence...")
        await page.locator("[ng-model='entitySelections.service.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name=re.compile("5[.]", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        # Step 3: Helsinki office
        print("Step 3: Helsinki office...")
        await page.locator("[data-ng-model='entitySelections.locality.value']").click()
        await page.wait_for_timeout(500)
        await page.get_by_role("option", name=re.compile("Helsinki.*Malmi", re.IGNORECASE)).click()
        await page.wait_for_timeout(1500)

        # Step 4: Search
        print("Step 4: Searching...")
        await page.locator("[data-ng-click='searchDesktop()']").click()
        await page.wait_for_timeout(8000)

        # Step 5: Parse the calendar table properly
        # The page shows a table where:
        # - Column headers are dates like "Ma 10.08." "Ti 11.08." etc
        # - Each cell contains time buttons like "08:15", "09:30" etc
        # We need to match each time to its date column

        print("Step 5: Parsing calendar table...")

        # Get all column headers (dates)
        # They appear as "Ma 10.08." "Ti 11.08." etc
        page_text = await page.inner_text("body")

        # Extract date headers from the calendar
        # Format: "Ma\n10.08." or "Ma 10.08."
        date_headers = re.findall(r'(?:Ma|Ti|Ke|To|Pe|La|Su)\s+(\d{1,2}\.\d{2}\.)', page_text)
        print(f"Date headers found: {date_headers}")

        # Current year
        year = datetime.now().year

        # Parse full dates
        parsed_dates = []
        for d in date_headers:
            try:
                # d is like "10.08." -> add year
                full = d.rstrip('.') + f".{year}"
                dt = datetime.strptime(full, "%d.%m.%Y").date()
                parsed_dates.append(dt)
            except Exception as e:
                print(f"  Date parse error: {d} -> {e}")

        print(f"Parsed dates: {parsed_dates}")

        # Now get the table cells - use JavaScript to read the table structure
        # This gives us each cell's column index and its time buttons
        result = await page.evaluate("""
            () => {
                const slots = [];
                // Find the results table
                const table = document.querySelector('table');
                if (!table) return {error: 'no table found'};

                // Get header row dates
                const headerCells = Array.from(table.querySelectorAll('th'));
                const dates = headerCells.map(th => th.innerText.trim());

                // Get data rows
                const rows = Array.from(table.querySelectorAll('tbody tr'));
                rows.forEach(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    cells.forEach((cell, colIndex) => {
                        const times = Array.from(cell.querySelectorAll('button'))
                            .map(btn => btn.innerText.trim())
                            .filter(t => /^\\d{1,2}:\\d{2}$/.test(t));
                        if (times.length > 0) {
                            slots.push({
                                date: dates[colIndex + 1] || 'unknown',
                                times: times
                            });
                        }
                    });
                });

                return {dates, slots};
            }
        """)

        print(f"JS table result: {str(result)[:500]}")

        if isinstance(result, dict) and result.get('slots'):
            for cell in result['slots']:
                date_text = cell['date']  # e.g. "Ma\n10.08."
                # Extract date like "10.08."
                date_match = re.search(r'(\d{1,2}\.\d{2}\.)', date_text)
                if date_match:
                    date_str = date_match.group(1).rstrip('.')
                    try:
                        full_date = datetime.strptime(f"{date_str}.{year}", "%d.%m.%Y").date()
                        for t in cell['times']:
                            slots.append({
                                'date': full_date,
                                'time': t,
                                'office': 'Helsinki (Malmi)',
                            })
                            print(f"  Slot: {full_date} {t} @ Helsinki (Malmi)")
                    except Exception as e:
                        print(f"  Parse error: {e}")
        else:
            # Fallback: match dates to times from page text
            print("Fallback: matching dates to times from page text...")
            # The page text has dates and times in order
            # Find blocks like "Ma\n10.08.\n14:45\n08:15..."
            blocks = re.findall(
                r'(?:Ma|Ti|Ke|To|Pe|La|Su)\s+(\d{1,2}\.\d{2}\.)\s*((?:\d{1,2}:\d{2}\s*)+)',
                page_text
            )
            for date_str, times_str in blocks:
                times = re.findall(r'\d{1,2}:\d{2}', times_str)
                date_str = date_str.rstrip('.')
                try:
                    full_date = datetime.strptime(f"{date_str}.{year}", "%d.%m.%Y").date()
                    for t in times:
                        slots.append({
                            'date': full_date,
                            'time': t,
                            'office': 'Helsinki (Malmi)',
                        })
                        print(f"  Fallback slot: {full_date} {t}")
                except Exception as e:
                    print(f"  Fallback parse error: {e}")

        await browser.close()
    return slots


async def main():
    checked_at = datetime.now().strftime("%d %b %Y at %H:%M UTC")
    print(f"Checking at {checked_at}, deadline {DEADLINE_DATE}")

    try:
        all_slots = await get_slots()
        print(f"Total slots found: {len(all_slots)}")

        # Filter by deadline
        early = [s for s in all_slots if s['date'] <= DEADLINE]
        later = [s for s in all_slots if s['date'] > DEADLINE]

        early.sort(key=lambda s: (s['date'], s['time']))
        later.sort(key=lambda s: (s['date'], s['time']))

        print(f"Early slots (before {DEADLINE_DATE}): {len(early)}")
        print(f"Later slots: {len(later)}")

        if early:
            lines = "\n".join(
                f"- {s['date'].strftime('%a %d.%m.%Y')} at {s['time']} @ {s['office']}"
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
            print(f"ALERT sent: {len(early)} early slots")

        elif later:
            earliest = later[0]
            msg = (
                f"Migri checked - no slots before {DEADLINE_DATE}\n\n"
                f"Earliest available:\n"
                f"{earliest['date'].strftime('%A %d.%m.%Y')} at {earliest['time']}\n"
                f"Office: {earliest['office']}\n\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print(f"No early slots. Earliest: {earliest['date']} {earliest['time']}")

        else:
            msg = (
                f"Migri checked - no appointments visible\n"
                f"Checked: {checked_at}"
            )
            telegram(msg)
            print("No slots found at all")

    except Exception as e:
        import traceback
        traceback.print_exc()
        telegram(f"Migri checker error:\n{str(e)[:300]}")


asyncio.run(main())
