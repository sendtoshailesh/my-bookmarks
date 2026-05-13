#!/usr/bin/env python3
"""
Scheduled Website Opener — opens URLs in the browser at configured times.

Uses macOS launchd (LaunchAgent) to run every minute. On each run, it checks
which scheduled URLs are "due" since the last check. This means if the laptop
was asleep at the scheduled time, the URL opens as soon as the laptop wakes.

Usage:
    python3 scheduled_opener.py --install     # Install LaunchAgent
    python3 scheduled_opener.py --uninstall   # Remove LaunchAgent
    python3 scheduled_opener.py --run         # Open all enabled URLs now
    python3 scheduled_opener.py --check       # Open URLs due since last check
    python3 scheduled_opener.py --status      # Show schedule and agent status
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import plistlib
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEDULE_FILE = SCRIPT_DIR / "schedule.json"
STATE_FILE = SCRIPT_DIR / ".opener_state.json"
PLIST_LABEL = "com.myBookmarks.scheduledOpener"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
LOG_DIR = Path.home() / ".local" / "log"
LOG_FILE = LOG_DIR / "scheduled-opener.log"

BROWSER_APPS = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "safari": "Safari",
    "default": None,
}


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_state() -> dict:
    """Load persisted state (last check time, opened occurrences)."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logging.warning("Corrupt state file, resetting.")
    return {}


def save_state(state: dict):
    """Persist state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def parse_time(time_str: str) -> datetime.time | None:
    """Parse HH:MM time string, handling both '7:00' and '07:00'."""
    for fmt in ("%H:%M", "%I:%M %p"):
        try:
            return datetime.datetime.strptime(time_str.strip(), fmt).time()
        except ValueError:
            continue
    logging.error("Invalid time format '%s' — expected HH:MM (24-hour)", time_str)
    return None


def validate_entry(entry: dict, index: int) -> bool:
    """Validate a schedule entry and log actionable errors."""
    if not entry.get("url"):
        logging.error("Schedule #%d: missing 'url' field — skipping.", index)
        return False
    if not entry.get("time"):
        logging.error("Schedule #%d (%s): missing 'time' — skipping.", index, entry.get("name", "Unnamed"))
        return False
    if parse_time(entry["time"]) is None:
        return False
    browser = entry.get("browser", "default").lower()
    if browser not in BROWSER_APPS:
        logging.warning("Schedule #%d: unknown browser '%s', will use system default.", index, browser)
    return True


def load_schedules() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        logging.error("Schedule file not found: %s", SCHEDULE_FILE)
        return []
    try:
        with open(SCHEDULE_FILE) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logging.error("Invalid JSON in %s: %s", SCHEDULE_FILE, e)
        return []
    entries = data.get("schedules", [])
    return [e for i, e in enumerate(entries) if validate_entry(e, i)]


def open_url(url: str, browser: str = "chrome"):
    """Open a URL in the specified browser on macOS."""
    app_name = BROWSER_APPS.get(browser.lower())
    if app_name:
        cmd = ["open", "-a", app_name, url]
    else:
        cmd = ["open", url]

    logging.info("Opening %s in %s", url, app_name or "default browser")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Failed to open %s: %s", url, e)


def get_due_schedules(
    schedules: list[dict],
    last_check: datetime.datetime,
    now: datetime.datetime,
) -> list[dict]:
    """Return schedule entries that were due between last_check and now.

    Instead of exact minute matching, this finds any schedule whose
    scheduled datetime falls in the (last_check, now] window. This
    handles sleep/wake catch-up naturally.
    """
    due = []
    # Check today and yesterday (covers overnight sleep)
    check_dates = {last_check.date(), now.date()}
    if last_check.date() != now.date():
        # Add all dates in range for multi-day sleep
        d = last_check.date()
        while d <= now.date():
            check_dates.add(d)
            d += datetime.timedelta(days=1)

    for entry in schedules:
        if not entry.get("enabled", True):
            continue

        sched_time = parse_time(entry["time"])
        if sched_time is None:
            continue

        days = [d.lower() for d in entry.get("days", list(
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        ))]

        for check_date in sorted(check_dates):
            day_name = check_date.strftime("%A").lower()
            if day_name not in days:
                continue
            scheduled_dt = datetime.datetime.combine(check_date, sched_time)
            if last_check < scheduled_dt <= now:
                due.append(entry)
                break  # Only open once per entry per check cycle

    return due


def cmd_check():
    """Open URLs due since last check. Called by launchd every minute."""
    now = datetime.datetime.now()
    state = load_state()

    last_check_str = state.get("last_check")
    if last_check_str:
        last_check = datetime.datetime.fromisoformat(last_check_str)
    else:
        # First run: set window to 2 minutes ago to avoid opening everything
        last_check = now - datetime.timedelta(minutes=2)

    schedules = load_schedules()
    due = get_due_schedules(schedules, last_check, now)

    opened_today = state.get("opened", {})
    today_key = now.strftime("%Y-%m-%d")

    for entry in due:
        occurrence_key = f"{entry.get('name', entry['url'])}|{today_key}|{entry['time']}"
        if occurrence_key in opened_today:
            logging.info("Already opened '%s' today at %s — skipping.", entry.get("name"), entry["time"])
            continue
        open_url(entry["url"], entry.get("browser", "default"))
        opened_today[occurrence_key] = now.isoformat()

    # Clean old entries (keep only today's)
    opened_today = {k: v for k, v in opened_today.items() if today_key in k}

    state["last_check"] = now.isoformat()
    state["opened"] = opened_today
    save_state(state)


def cmd_run():
    """Open all enabled URLs immediately, ignoring schedule times."""
    schedules = load_schedules()
    opened = 0
    for entry in schedules:
        if entry.get("enabled", True):
            open_url(entry["url"], entry.get("browser", "default"))
            opened += 1
    logging.info("Opened %d URL(s).", opened)


def cmd_install():
    """Install the launchd LaunchAgent plist."""
    python_path = sys.executable
    script_path = str(Path(__file__).resolve())

    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [python_path, script_path, "--check"],
        "StartInterval": 60,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
    }

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    # Unload first (ignore errors if not loaded)
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        logging.info("✅ LaunchAgent installed and loaded: %s", PLIST_PATH)
        logging.info("   Checks every 60 seconds for scheduled URLs.")
    else:
        logging.error("Failed to load LaunchAgent: %s", result.stderr)
        sys.exit(1)


def cmd_uninstall():
    """Remove the launchd LaunchAgent."""
    if PLIST_PATH.exists():
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True,
        )
        PLIST_PATH.unlink()
        logging.info("✅ LaunchAgent uninstalled: %s", PLIST_PATH)
    else:
        logging.info("LaunchAgent not installed (plist not found).")


def cmd_status():
    """Show current schedules and LaunchAgent status."""
    # Agent status
    if PLIST_PATH.exists():
        result = subprocess.run(
            ["launchctl", "list", PLIST_LABEL],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"🟢 LaunchAgent is loaded ({PLIST_LABEL})")
        else:
            print(f"🟡 Plist exists but agent not loaded ({PLIST_PATH})")
    else:
        print("🔴 LaunchAgent not installed")

    print()

    # Schedules
    schedules = load_schedules()
    if not schedules:
        print("No schedules configured.")
        return

    print(f"📋 {len(schedules)} schedule(s):\n")
    for entry in schedules:
        status = "✅" if entry.get("enabled", True) else "⏸️"
        days = ", ".join(d.capitalize() for d in entry.get("days", []))
        print(f"  {status} {entry.get('name', 'Unnamed')}")
        print(f"     URL:     {entry.get('url', '—')}")
        print(f"     Browser: {entry.get('browser', 'default')}")
        print(f"     Time:    {entry.get('time', '—')}")
        print(f"     Days:    {days}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Scheduled Website Opener — open URLs at configured times."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="Install LaunchAgent")
    group.add_argument("--uninstall", action="store_true", help="Remove LaunchAgent")
    group.add_argument("--run", action="store_true", help="Open all enabled URLs now")
    group.add_argument(
        "--check", action="store_true", help="Open URLs matching current time"
    )
    group.add_argument(
        "--status", action="store_true", help="Show schedule and agent status"
    )

    args = parser.parse_args()
    setup_logging()

    if args.install:
        cmd_install()
    elif args.uninstall:
        cmd_uninstall()
    elif args.run:
        cmd_run()
    elif args.check:
        cmd_check()
    elif args.status:
        cmd_status()


if __name__ == "__main__":
    main()
