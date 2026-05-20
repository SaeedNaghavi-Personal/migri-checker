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
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": message
            },
            timeout=15,
        )
        print(f"[telegram] status={r.status_code}")
    except Exception as e:
        print(f"[telegram] error: {e}")


async def get_week_slots(page):
    """
    Much more tolerant slot extraction.

    Instead of depending on exact DOM structure,
    we search nearby text for HH:MM patterns.
    """

    slots = await page.evaluate(f"""
    () => {{
        const results = [];
        const year = {YEAR};

        // Find all visible date labels like 10.08.
        const all = [...document.querySelectorAll('*')];

        const dateHeaders = all.filter(el => {{
            const txt = el.innerText?.trim() || '';
            return /^\\d{{1,2}}\\.\\d{{2}}\\.$/.test(txt);
        }});

        console.log("Date headers:", dateHeaders.length);

        for (const dateEl of dateHeaders) {{

            const dateText = dateEl.innerText.trim();

            const parts = dateText
                .replace(/\\.$/, '')
                .split('.');

            if (parts.length < 2) continue;

            const day = parts[0];
            const month = parts[1];

            const fullDate =
                `${{year}}-${{String(month).padStart(2, '0')}}-${{String(day).padStart(2, '0')}}`;

            // Walk upward and scan nearby text
            let parent = dateEl.parentElement;

            for (let level = 0; level < 8 && parent; level++) {{

                const txt = parent.innerText || '';

                // Find ALL HH:MM occurrences
                const matches = [...txt.matchAll(/\\b\\d{{1,2}}:\\d{{2}}\\b/g)];

                for (const match of matches) {{
                    results.push({{
                        date: fullDate,
                        time: match[0]
                    }});
                }}

                parent = parent.parentElement;
            }}
        }}

        // Remove duplicates
        const unique = results.filter(
            (v, i, a) =>
                a.findIndex(
                    t => t.date === v.date && t.time === v.time
                ) === i
        );

        console.log("Found slots:", unique);

        return unique;
    }}
    """)

    return slots


async def get_all_slots():
    from playwright.async_api import async_playwright

    all_slots = []

    async with async_playwright() as pw:

        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        page = await browser.new_page(
            viewport={"width": 1600, "height": 1200},
            locale="fi-FI",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        )

        print("Loading Migri...")

        await page.goto(
            MIGRI_URL,
            wait_until="networkidle",
            timeout=90000
        )

        await page.wait_for_timeout(5000)

        # Optional screenshot for debugging
        await page.screenshot(
            path="migri_loaded.png",
            full_page=True
        )

        # ---------------------------------------------------
        # STEP 1
        # ---------------------------------------------------

        print("Step 1: Residence permit...")

        await page.locator(
            "[ng-model='entitySelections.category.value']"
        ).click()

        await page.wait_for_timeout(1000)

        await page.get_by_role(
            "option",
            name="Oleskelulupa"
        ).click()

        await page.wait_for_timeout(2000)

        # ---------------------------------------------------
        # STEP 2
        # ---------------------------------------------------

        print("Step 2: Permanent residence...")

        await page.locator(
            "[ng-model='entitySelections.service.value']"
        ).click()

        await page.wait_for_timeout(1000)

        await page.get_by_role(
            "option",
            name=re.compile(r"5[.]", re.IGNORECASE)
        ).click()

        await page.wait_for_timeout(2500)

        # ---------------------------------------------------
        # STEP 3
        # ---------------------------------------------------

        print("Step 3: Helsinki office...")

        await page.locator(
            "[data-ng-model='entitySelections.locality.value']"
        ).click()

        await page.wait_for_timeout(1000)

        await page.get_by_role(
            "option",
            name=re.compile(r"Helsinki.*Malmi", re.IGNORECASE)
        ).click()

        await page.wait_for_timeout(2500)

        # ---------------------------------------------------
        # SEARCH
        # ---------------------------------------------------

        print("Step 4: Searching...")

        await page.locator(
            "[data-ng-click='searchDesktop()']"
        ).click()

        await page.wait_for_timeout(8000)

        # Debug screenshot after search
        await page.screenshot(
            path="migri_search_results.png",
            full_page=True
        )

        # ---------------------------------------------------
        # WEEKS LOOP
        # ---------------------------------------------------

        for week_num in range(20):

            print(f"\n===== WEEK {week_num + 1} =====")

            page_text = await page.inner_text("body")

            week_dates = re.findall(
                r'\\b(\\d{1,2}\\.\\d{2})\\.\\s',
                page_text
            )

            print("Visible dates:", week_dates[:7])

            # ------------------------------------------------
            # GET SLOTS
            # ------------------------------------------------

            week_slots = await get_week_slots(page)

            print(f"Slots detected this week: {len(week_slots)}")

            for s in week_slots:
                try:
                    dt = datetime.strptime(
                        s["date"],
                        "%Y-%m-%d"
                    ).date()

                    print(f"  FOUND: {dt} {s['time']}")

                    all_slots.append({
                        "date": dt,
                        "time": s["time"],
                        "office": "Helsinki (Malmi)"
                    })

                except Exception as e:
                    print("Parse error:", e)

            # ------------------------------------------------
            # STOP AFTER DEADLINE
            # ------------------------------------------------

            stop = False

            for d in week_dates[:7]:
                try:
                    dt = datetime.strptime(
                        f"{d}.{YEAR}",
                        "%d.%m.%Y"
                    ).date()

                    if dt > DEADLINE:
                        stop = True

                except:
                    pass

            if stop:
                print("Reached past deadline.")
                break

            # ------------------------------------------------
            # NEXT WEEK
            # ------------------------------------------------

            next_btn = page.locator(
                "[data-ng-click='nextWeek()']:not([id*='mobile'])"
            ).first

            await next_btn.click()

            print("Clicked next week")

            await page.wait_for_timeout(3000)

        await browser.close()

    return all_slots


async def main():

    checked_at = datetime.utcnow().strftime(
        "%d %b %Y at %H:%M UTC"
    )

    print(
        f"Checking at {checked_at}, "
        f"deadline {DEADLINE_DATE}"
    )

    try:

        all_slots = await get_all_slots()

        print(f"\nTOTAL SLOTS FOUND: {len(all_slots)}")

        # Remove duplicates
        unique = []
        seen = set()

        for s in all_slots:
            key = (s["date"], s["time"])

            if key not in seen:
                seen.add(key)
                unique.append(s)

        all_slots = sorted(
            unique,
            key=lambda x: (x["date"], x["time"])
        )

        early = [
            s for s in all_slots
            if s["date"] <= DEADLINE
        ]

        later = [
            s for s in all_slots
            if s["date"] > DEADLINE
        ]

        print(f"Before deadline: {len(early)}")
        print(f"After deadline: {len(later)}")

        # ---------------------------------------------------
        # FOUND BEFORE DEADLINE
        # ---------------------------------------------------

        if early:

            lines = "\n".join(
                f"- {s['date'].strftime('%a %d.%m.%Y')} at {s['time']}"
                for s in early[:10]
            )

            msg = (
                f"MIGRI SLOT FOUND!\n\n"
                f"Helsinki (Malmi)\n\n"
                f"{len(early)} slot(s) before {DEADLINE_DATE}:\n\n"
                f"{lines}\n\n"
                f"Book now:\n"
                f"migri.vihta.com\n\n"
                f"Checked: {checked_at}"
            )

            telegram(msg)

            print("ALERT SENT")

        # ---------------------------------------------------
        # ONLY LATER SLOTS
        # ---------------------------------------------------

        elif later:

            earliest = later[0]

            msg = (
                f"No slots before {DEADLINE_DATE}\n\n"
                f"Earliest available:\n"
                f"{earliest['date'].strftime('%A %d.%m.%Y')} "
                f"at {earliest['time']}\n\n"
                f"Checked: {checked_at}"
            )

            telegram(msg)

            print("Only later slots found")

        # ---------------------------------------------------
        # NOTHING
        # ---------------------------------------------------

        else:

            telegram(
                f"Migri checked - no appointments visible\n"
                f"Checked: {checked_at}"
            )

            print("No slots found at all")

    except Exception as e:

        import traceback

        traceback.print_exc()

        telegram(
            f"Migri checker error:\n"
            f"{str(e)[:300]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
