import asyncio
import re
import os
from datetime import datetime, UTC
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT = os.environ["TELEGRAM_CHAT"]

DEADLINE_DATE = "2026-08-12"

MIGRI_URL = "https://migri.vihta.com/public/migri/#/reservation"

DEADLINE = datetime.strptime(
    DEADLINE_DATE,
    "%Y-%m-%d"
).date()

YEAR = datetime.now().year


# =========================================================
# TELEGRAM
# =========================================================

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


# =========================================================
# SLOT EXTRACTION
# =========================================================

async def get_week_slots(page):

    slots = await page.evaluate(f"""
    () => {{

        const results = [];
        const year = {YEAR};

        // -------------------------------------------------
        // Find visible date headers
        // -------------------------------------------------

        const allEls = [...document.querySelectorAll('*')];

        const dateHeaders = allEls.filter(el => {{

            const txt = el.innerText?.trim() || '';

            return (
                /^\\d{{1,2}}\\.\\d{{2}}\\.$/.test(txt)
                &&
                el.offsetParent !== null
            );
        }});

        console.log("DATE HEADERS FOUND:", dateHeaders.length);

        // -------------------------------------------------
        // Process each date column
        // -------------------------------------------------

        for (const dateEl of dateHeaders) {{

            const dateText = dateEl.innerText.trim();

            const parts = dateText
                .replace(/\\.$/, '')
                .split('.');

            if (parts.length < 2)
                continue;

            const day = parseInt(parts[0]);
            const month = parseInt(parts[1]);

            const fullDate =
                `${{year}}-${{String(month).padStart(2,'0')}}-${{String(day).padStart(2,'0')}}`;

            console.log("CHECKING DATE:", fullDate);

            // ---------------------------------------------
            // Walk upward to find the correct day column
            // ---------------------------------------------

            let column = dateEl;

            for (let level = 0; level < 6; level++) {{

                if (!column.parentElement)
                    break;

                column = column.parentElement;

                // Get all buttons and links
                const buttons = [
                    ...column.querySelectorAll('button, a')
                ];

                // Extract ONLY exact HH:MM text
                const times = buttons
                    .map(btn => btn.innerText?.trim() || '')
                    .filter(txt =>
                        /^\\d{{1,2}}:\\d{{2}}$/.test(txt)
                    );

                // Count date labels inside this container
                const datesInside = [
                    ...column.querySelectorAll('*')
                ].filter(el => {{

                    const txt = el.innerText?.trim() || '';

                    return /^\\d{{1,2}}\\.\\d{{2}}\\.$/.test(txt);
                }});

                // -----------------------------------------
                // GOOD COLUMN FOUND
                // -----------------------------------------

                if (
                    times.length > 0
                    &&
                    datesInside.length <= 1
                ) {{

                    const uniqueTimes = [...new Set(times)];

                    console.log(
                        "FOUND COLUMN:",
                        fullDate,
                        uniqueTimes
                    );

                    for (const t of uniqueTimes) {{

                        results.push({{
                            date: fullDate,
                            time: t
                        }});
                    }}

                    break;
                }}
            }}
        }}

        // -------------------------------------------------
        // Remove duplicates
        // -------------------------------------------------

        const unique = results.filter(
            (v, i, a) =>
                a.findIndex(
                    t =>
                        t.date === v.date
                        &&
                        t.time === v.time
                ) === i
        );

        console.log("FINAL UNIQUE SLOTS:", unique);

        return unique;
    }}
    """)

    return slots


# =========================================================
# MAIN SCRAPER
# =========================================================

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
            viewport={
                "width": 1600,
                "height": 1200
            },
            locale="fi-FI",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        )

        # -------------------------------------------------
        # LOAD PAGE
        # -------------------------------------------------

        print("Loading Migri...")

        await page.goto(
            MIGRI_URL,
            wait_until="networkidle",
            timeout=90000
        )

        await page.wait_for_timeout(5000)

        await page.screenshot(
            path="01_loaded.png",
            full_page=True
        )

        # -------------------------------------------------
        # STEP 1
        # -------------------------------------------------

        print("Step 1: Residence permit...")

        await page.locator(
            "[ng-model='entitySelections.category.value']"
        ).click()

        await page.wait_for_timeout(1000)

        await page.get_by_role(
            "option",
            name="Oleskelulupa"
        ).click()

        await page.wait_for_timeout(2500)

        # -------------------------------------------------
        # STEP 2
        # -------------------------------------------------

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

        # -------------------------------------------------
        # STEP 3
        # -------------------------------------------------

        print("Step 3: Helsinki office...")

        await page.locator(
            "[data-ng-model='entitySelections.locality.value']"
        ).click()

        await page.wait_for_timeout(1000)

        await page.get_by_role(
            "option",
            name=re.compile(
                r"Helsinki.*Malmi",
                re.IGNORECASE
            )
        ).click()

        await page.wait_for_timeout(2500)

        # -------------------------------------------------
        # SEARCH
        # -------------------------------------------------

        print("Step 4: Searching...")

        await page.locator(
            "[data-ng-click='searchDesktop()']"
        ).click()

        await page.wait_for_timeout(8000)

        await page.screenshot(
            path="02_search_results.png",
            full_page=True
        )

        # =================================================
        # WEEKS LOOP
        # =================================================

        for week_num in range(20):

            print(f"\\n===== WEEK {week_num + 1} =====")

            # ---------------------------------------------
            # DEBUG HTML
            # ---------------------------------------------

            html = await page.content()

            with open(
                f"debug_week_{week_num+1}.html",
                "w",
                encoding="utf-8"
            ) as f:
                f.write(html)

            # ---------------------------------------------
            # PAGE TEXT
            # ---------------------------------------------

            page_text = await page.inner_text("body")

            week_dates = re.findall(
                r'(\\d{1,2}\\.\\d{2})\\.',
                page_text
            )

            print("Visible dates:", week_dates[:7])

            # ---------------------------------------------
            # EXTRACT SLOTS
            # ---------------------------------------------

            week_slots = await get_week_slots(page)

            print(
                f"Slots detected this week: "
                f"{len(week_slots)}"
            )

            for s in week_slots:

                try:

                    dt = datetime.strptime(
                        s["date"],
                        "%Y-%m-%d"
                    ).date()

                    print(
                        f"  FOUND: "
                        f"{s['date']} "
                        f"{s['time']}"
                    )

                    all_slots.append({
                        "date": dt,
                        "time": s["time"],
                        "office": "Helsinki (Malmi)"
                    })

                except Exception as e:

                    print("Parse error:", e)

            # ---------------------------------------------
            # DEADLINE CHECK
            # ---------------------------------------------

            if week_dates:

                try:

                    latest_visible = max([
                        datetime.strptime(
                            f"{d}.{YEAR}",
                            "%d.%m.%Y"
                        ).date()
                        for d in week_dates[:7]
                    ])

                    print(
                        "Latest visible:",
                        latest_visible
                    )

                    if latest_visible > DEADLINE:
                        print(
                            "Reached past deadline."
                        )
                        break

                except Exception as e:
                    print("Deadline parse error:", e)

            # ---------------------------------------------
            # NEXT WEEK
            # ---------------------------------------------

            next_btn = page.locator(
                "[data-ng-click='nextWeek()']:not([id*='mobile'])"
            ).first

            await next_btn.click()

            print("Clicked next week")

            await page.wait_for_timeout(3000)

        await browser.close()

    return all_slots


# =========================================================
# MAIN
# =========================================================

async def main():

    checked_at = datetime.now(UTC).strftime(
        "%d %b %Y at %H:%M UTC"
    )

    print(
        f"Checking at {checked_at}, "
        f"deadline {DEADLINE_DATE}"
    )

    try:

        all_slots = await get_all_slots()

        print(
            f"\\nTOTAL RAW SLOTS: "
            f"{len(all_slots)}"
        )

        # -------------------------------------------------
        # REMOVE DUPLICATES
        # -------------------------------------------------

        unique = []
        seen = set()

        for s in all_slots:

            key = (
                s["date"],
                s["time"]
            )

            if key not in seen:

                seen.add(key)
                unique.append(s)

        all_slots = sorted(
            unique,
            key=lambda s: (
                s["date"],
                s["time"]
            )
        )

        print(
            f"TOTAL UNIQUE SLOTS: "
            f"{len(all_slots)}"
        )

        # -------------------------------------------------
        # SPLIT EARLY/LATE
        # -------------------------------------------------

        early = [
            s for s in all_slots
            if s["date"] <= DEADLINE
        ]

        later = [
            s for s in all_slots
            if s["date"] > DEADLINE
        ]

        print(
            f"Before deadline: {len(early)}"
        )

        print(
            f"After deadline: {len(later)}"
        )

        # -------------------------------------------------
        # EARLY SLOTS FOUND
        # -------------------------------------------------

        if early:

            lines = "\\n".join(
                f"- {s['date'].strftime('%a %d.%m.%Y')} at {s['time']}"
                for s in early[:10]
            )

            msg = (
                f"MIGRI SLOT FOUND!\\n\\n"
                f"Helsinki (Malmi)\\n\\n"
                f"{len(early)} slot(s) before "
                f"{DEADLINE_DATE}:\\n\\n"
                f"{lines}\\n\\n"
                f"Book now:\\n"
                f"migri.vihta.com\\n\\n"
                f"Checked: {checked_at}"
            )

            telegram(msg)

            print("ALERT SENT")

        # -------------------------------------------------
        # ONLY LATER SLOTS
        # -------------------------------------------------

        elif later:

            earliest = later[0]

            msg = (
                f"No slots before "
                f"{DEADLINE_DATE}\\n\\n"
                f"Earliest available:\\n"
                f"{earliest['date'].strftime('%A %d.%m.%Y')} "
                f"at {earliest['time']}\\n\\n"
                f"Checked: {checked_at}"
            )

            telegram(msg)

            print("Only later slots found")

        # -------------------------------------------------
        # NOTHING FOUND
        # -------------------------------------------------

        else:

            telegram(
                f"Migri checked - no appointments visible\\n"
                f"Checked: {checked_at}"
            )

            print("No slots found at all")

    except Exception as e:

        import traceback

        traceback.print_exc()

        telegram(
            f"Migri checker error:\\n"
            f"{str(e)[:300]}"
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
