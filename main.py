#!/usr/bin/env python3
"""
Migri Appointment Checker — Direct API version
No browser needed. Calls the Vihta JSON API directly.
Gets exact appointment times, not just dates.

Service: Permanent Residence Permit (5.)
Office:  Helsinki service point
"""

import sys
import time
from datetime import datetime
import requests

# ─────────────────────────────────────────
# YOUR SETTINGS — only edit these
# ─────────────────────────────────────────
DEADLINE_DATE       = "2026-07-12"        # Alert if slot found before this date
NTFY_TOPIC          = "Migri_Appointment" # Your ntfy topic name
CHECK_INTERVAL_SECS = 900                 # 900 = every 15 minutes
# ─────────────────────────────────────────

# Migri/Vihta API — fixed IDs, do not change
SERVICE_ID  = "3e03034d-a44b-4771-b1e5-2c4a6f581b7d"  # 5. Permanent residence permit
OFFICE_ID   = "25ee3bce-aec9-41a7-b920-74dc09112dd4"  # Helsinki service point
OFFICE_NAME = "Helsinki service point"
MIGRI_URL   = "https://migri.vihta.com/public/migri/#/reservation"
API_BASE    = "https://migri.vihta.com/public/migri/api"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://migri.vihta.com",
    "Referer": "https://migri.vihta.com/public/migri/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

DEADLINE = datetime.strptime(DEADLINE_DATE, "%Y-%m-%d").date()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify(title, message, priority="default"):
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "calendar",
                "Click": MIGRI_URL,
            },
            timeout=10,
        )
        print(f"[ntfy] '{title}' → {r.status_code}")
    except Exception as e:
        print(f"[ntfy] Error: {e}")


def get_slots():
    """Call Vihta API and return list of available slot dicts."""
    slots = []
    endpoints = [
        f"{API_BASE}/reservations/times?serviceId={SERVICE_ID}&officeId={OFFICE_ID}&numberOfCustomers=1",
        f"{API_BASE}/services/{SERVICE_ID}/offices/{OFFICE_ID}/times?numberOfCustomers=1",
        f"{API_BASE}/offices/{OFFICE_ID}/services/{SERVICE_ID}/available-times?customers=1",
    ]

    for url in endpoints:
        try:
            print(f"[{now()}] Trying: {url}")
            r = requests.get(url, headers=HEADERS, timeout=20)
            print(f"[{now()}] Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"[{now()}] Response: {str(data)[:400]}")
                slots = parse_slots(data)
                if slots or data:  # got a real response, stop trying
                    break
        except Exception as e:
            print(f"[{now()}] Error on {url}: {e}")

    return slots


def parse_slots(data):
    slots = []
    items = []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ["times", "slots", "availableTimes", "reservationTimes", "data", "results"]:
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        if not items:
            items = [data] if "startTime" in data or "time" in data or "date" in data else []

    for item in items:
        try:
            dt = None
            for key in ["startTime", "start", "time", "dateTime", "datetime", "date", "startDate"]:
                val = item.get(key)
                if val and isinstance(val, str) and len(val) >= 10:
                    try:
                        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                        break
                    except Exception:
                        try:
                            dt = datetime.strptime(val[:19], "%Y-%m-%dT%H:%M:%S")
                            break
                        except Exception:
                            pass
            if dt:
                slots.append({
                    "datetime": dt,
                    "office": item.get("officeName") or item.get("office") or OFFICE_NAME,
                    "address": item.get("address") or item.get("officeAddress") or "Helsinki",
                })
        except Exception as e:
            print(f"[{now()}] Parse error: {e}")

    return slots


def fmt(slot):
    dt = slot["datetime"]
    return (
        f"{dt.strftime('%A, %d %B %Y')} at {dt.strftime('%H:%M')}\n"
        f"📍 {slot['office']}"
    )


def main():
    print("=" * 55)
    print("  Migri Appointment Checker (API mode)")
    print(f"  Deadline : {DEADLINE_DATE}")
    print(f"  Interval : {CHECK_INTERVAL_SECS // 60} min")
    print(f"  ntfy     : {NTFY_TOPIC}")
    print("=" * 55)

    notify(
        "Migri Checker started ✅",
        f"Watching Helsinki PR appointments before {DEADLINE_DATE}.\n"
        f"Checking every {CHECK_INTERVAL_SECS // 60} min.",
        priority="low"
    )

    check_num = 0
    while True:
        check_num += 1
        checked_at = datetime.now().strftime("%d %b %Y at %H:%M")
        print(f"\n[{now()}] ── Check #{check_num} ──")

        try:
            all_slots = get_slots()
            all_slots.sort(key=lambda s: s["datetime"])
            print(f"[{now()}] Found {len(all_slots)} total slots")

            early = [s for s in all_slots if s["datetime"].date() <= DEADLINE]
            later = [s for s in all_slots if s["datetime"].date() > DEADLINE]

            if early:
                earliest = early[0]
                lines = "\n".join(f"• {fmt(s)}" for s in early[:5])
                msg = (
                    f"Earliest slot:\n{fmt(earliest)}\n\n"
                    f"All {len(early)} slot(s) before {DEADLINE_DATE}:\n{lines}\n\n"
                    f"👉 Book at migri.vihta.com\n"
                    f"Checked: {checked_at}"
                )
                print(f"[{now()}] 🚨 {len(early)} early slots found!")
                notify("🚨 Migri slot available — book NOW!", msg, priority="urgent")

            elif later:
                earliest = later[0]
                msg = (
                    f"No slots before {DEADLINE_DATE}.\n\n"
                    f"Earliest available:\n{fmt(earliest)}\n\n"
                    f"Checked: {checked_at}"
                )
                print(f"[{now()}] No early slots. Earliest: {earliest['datetime'].strftime('%d %b %Y %H:%M')}")
                notify("Migri checked — no early slots yet", msg, priority="low")

            else:
                msg = (
                    f"No appointments showing on Migri.\n"
                    f"Calendar may be fully booked or new slots not released yet.\n\n"
                    f"Checked: {checked_at}"
                )
                print(f"[{now()}] No slots found at all")
                notify("Migri checked — calendar empty", msg, priority="low")

        except Exception as e:
            print(f"[{now()}] Unexpected error: {e}")
            notify("Migri checker error ⚠️", f"Check #{check_num} failed:\n{str(e)[:300]}", priority="low")

        print(f"[{now()}] Sleeping {CHECK_INTERVAL_SECS // 60} min...")
        time.sleep(CHECK_INTERVAL_SECS)


if __name__ == "__main__":
    main()
