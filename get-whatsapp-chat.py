
from datetime import date, timedelta
from pathlib import Path
import json
import re
import sys

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ---------- Configuration ----------

GROUPS = [
    {"name": "College group chat", "from_date": "2026-10-10"},
    {"name": "Study group chat", "from_date": "2026-10-23"},
]

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_DIR = SCRIPT_DIR / "exports"
PROFILE_DIR = SCRIPT_DIR / "whatsapp-profile"
STATE_FILE = SCRIPT_DIR / ".whatsapp_export_state.json"

EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not save export state: {exc}", file=sys.stderr)


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise ValueError("Group name produces an invalid filename.")
    return name



def export_group(page, group, state):
    group_name = group["name"]
    key = group_name.strip().casefold()
    saved = state.get(key)
    if saved:
        try:
            from_date = date.fromisoformat(saved) + timedelta(days=1)
        except ValueError:
            from_date = date.fromisoformat(group["from_date"])
    else:
        from_date = date.fromisoformat(group["from_date"])
    to_date = date.today()
    if from_date > to_date:
        print(f"\n--- Skipping: {group_name} ---")
        print("No new days to export (from_date would be after today).")
        return None

    output_file = EXPORT_DIR / f"{safe_filename(group_name)}.zip"

    print(f"\n--- Exporting: {group_name} ---")
    print(f"Date range: {from_date} to {to_date}")

    # Dismiss any leftover menus or dialogs from the previous export.
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    # If the search box is not visible, reload WhatsApp Web.
    search_box = page.get_by_role(
        "textbox",
        name="Search or start a new chat",
        exact=True,
    )

    if not search_box.is_visible():
        print("Search box unavailable; reloading WhatsApp Web...")
        page.goto(
            "https://web.whatsapp.com",
            wait_until="domcontentloaded",
        )
        search_box.wait_for(state="visible", timeout=60000)

    # Search for the group.
    search_box.fill(group_name)

    group_result = page.get_by_text(group_name, exact=True).first
    group_result.wait_for(state="visible", timeout=15000)
    group_result.click()

    # Open the menu in the conversation header.
    chat_header = page.get_by_test_id("conversation-header")
    menu_button = chat_header.get_by_role(
        "button",
        name="Menu",
        exact=True,
    )
    menu_button.click()

    # Open the export dialog.
    page.get_by_text("Export chat", exact=True).click()

    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible", timeout=10000)

    # Select the custom date range and fill both dates.
    dialog.get_by_text("Custom date range", exact=True).click()
    dialog.get_by_label("Start date").fill(from_date.isoformat())
    dialog.get_by_label("End date").fill(to_date.isoformat())

    # Capture the download triggered by clicking Export.
    with page.expect_download(timeout=60000) as download_info:
        dialog.get_by_role(
            "button",
            name="Export",
            exact=True,
        ).click()

    download = download_info.value
    download.save_as(str(output_file))
    print(f"Saved: {output_file}")
    return to_date


with sync_playwright() as p:
    state = _load_state()
    updated = False
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        accept_downloads=True,
    )

    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

        # Wait for WhatsApp to be ready; first run may require QR login.
        page.get_by_role(
            "textbox",
            name="Search or start a new chat",
            exact=True,
        ).wait_for(state="visible", timeout=60000)

        successful = []
        failed = []

        for index, group in enumerate(GROUPS):
            try:
                end_date = export_group(page, group, state)
                if end_date is None:
                    continue
                key = group["name"].strip().casefold()
                state[key] = end_date.isoformat()
                updated = True
                successful.append(group["name"])

            except Exception as exc:
                failed.append((group["name"], str(exc)))
                print(f"FAILED: {group['name']}: {exc}")

                # Close any leftover menu/dialog before the next attempt.
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass

            # Brief pause between groups; no need to restart WhatsApp.
            if index < len(GROUPS) - 1:
                page.wait_for_timeout(2000)

        print("\n========== SUMMARY ==========")
        print(f"Successful exports: {len(successful)}")
        for name in successful:
            print(f"  OK: {name}")

        print(f"Failed exports: {len(failed)}")
        for name, error in failed:
            print(f"  FAILED: {name} — {error}")
        if updated:
            _save_state(state)
            print(f"\nUpdated {STATE_FILE.name} so next run starts from the next day.")

    finally:
        context.close()
