import asyncio
import re
import os
from datetime import datetime, UTC
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT = os.environ["TELEGRAM_CHAT"]

DEADLINE_DATE = "2026-08-12"

MIGRI_URL = "https://migri.vihta.com/public/migri/#/reservation"

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

    slots = await page.evaluate(f"""
    () => {{

        const results = [];

        // Find clickable appointment buttons

        const buttons = [
            ...document.querySelectorAll('button')
        ];

        for (const btn of buttons) {{

            const txt = btn.innerText?.trim() || '';

            // Exact time buttons only
            if (!/^\\d{{1,2}}:\\d{{2}}$/.test(txt))
                continue;

            // Find nearby date in same column
            let parent = btn.parentElement;

            for (let i = 0; i < 8 && parent; i++) {{

                const dates = [
                    ...parent.querySelectorAll('*')
                ]
                .map(el => el.innerText?.trim() || '')
                .filter(t =>
                    /^\\d{{1,2}}\\.\\d{{2}}\\.$/.test(t)
                );

                if (dates.length === 1) {{

                    const dateText = dates[0];

                    const parts = dateText
                        .replace(/\\.$/, '')
                        .split('.');

                    const day = parts[0];
                    const month = parts[1];

                    const fullDate =
                        `${{YEAR}}-${{month.padStart(2,'0')}}-${{day.padStart(2,'0')}}`;

                    results.push({{
                        date: fullDate,
                        time: txt
                    }});

                    break;
                }}

                parent = parent.parentElement;
            }}
        }}

        // dedupe

        return results.filter(
            (v, i, a) =>
                a.findIndex(
                    t =>
                        t.date === v.date
                        &&
                        t.time === v.time
                ) === i
        );
    }}
    """)

    return slots


async def main():

    from playwright.async_api import async_playwright

    checked_at = datetime.now(UTC).strftime(
        "%d %b %Y at %H:%M UTC"
    )

    print(
        f"Checking at {checked_at}, "
        f"deadline {DEADLINE_DATE}"
    )

    async with async_playwright() as pw:

        browser = await pw.chromium.launch(
            headless=False,
            slow_mo=300,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )

        page = await browser.new_page(
            viewport={
                "width": 1600,
                "height": 1200
            }
        )

        print("Loading Migri...")

        await page.goto(
            MIGRI_URL,
            wait_until="domcontentloaded",
            timeout=90000
        )

        # -------------------------------------------------
        # ACCEPT COOKIE IF EXISTS
        # -------------------------------------------------

        try:

            cookie_btn = page.get_by_role(
                "button",
                name=re.compile(
                    "Accept|Hyväksy",
                    re.IGNORECASE
                )
            )

            if await cookie_btn.count() > 0:
                await cookie_btn.first.click()
                print("Cookie banner accepted")

        except:
            pass

        # -------------------------------------------------
        # STEP 1
        # -------------------------------------------------

        print("Step 1")

        await page.locator(
            "[ng-model='entitySelections.category.value']"
        ).click()

        await page.get_by_role(
            "option",
            name="Oleskelulupa"
        ).click()

        # -------------------------------------------------
        # STEP 2
        # -------------------------------------------------

        print("Step 2")

        await page.locator(
            "[ng-model='entitySelections.service.value']"
        ).click()

        await page.get_by_role(
            "option",
            name=re.compile(r"5[.]")
        ).click()

        # -------------------------------------------------
        # STEP 3
        # -------------------------------------------------

        print("Step 3")

        await page.locator(
            "[data-ng-model='entitySelections.locality.value']"
        ).click()

        await page.get_by_role(
            "option",
            name=re.compile(
                "Helsinki.*Malmi",
                re.IGNORECASE
            )
        ).click()

        # -------------------------------------------------
        # SEARCH
        # -------------------------------------------------

        print("SEARCH")

        await page.locator(
            "[data-ng-click='searchDesktop()']"
        ).click()

        # IMPORTANT:
        # WAIT FOR REAL CALENDAR

        await page.wait_for_selector(
            "button",
            timeout=60000
        )

        await page.wait_for_timeout(5000)

        await page.screenshot(
            path="calendar.png",
            full_page=True
        )

        all_slots = []

        for week in range(20):

            print(f"\\nWEEK {week+1}")

            slots = await get_week_slots(page)

            print("FOUND:", len(slots))

            for s in slots:

                print(
                    s["date"],
                    s["time"]
                )

                all_slots.append(s)

            next_btn = page.locator(
                "[data-ng-click='nextWeek()']"
            ).first

            await next_btn.click()

            await page.wait_for_timeout(2500)

        print("\\nTOTAL:", len(all_slots))

        if all_slots:

            lines = "\\n".join([
                f"{s['date']} {s['time']}"
                for s in all_slots[:10]
            ])

            telegram(
                f"Migri slots found!\\n\\n{lines}"
            )

        else:

            telegram(
                f"No slots found\\nChecked: {checked_at}"
            )

        await browser.close()


asyncio.run(main())
