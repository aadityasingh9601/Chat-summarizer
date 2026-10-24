
from datetime import date
from pathlib import Path
import re

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ---------- Configuration ----------

GROUPS = [
    {"name": "Aryan Kushwaha", "from_date": "2026-10-10"},
    {"name": "Nawab", "from_date": "2026-10-23"},
]

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_DIR = SCRIPT_DIR / "exports"
PROFILE_DIR = SCRIPT_DIR / "whatsapp-profile"

EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise ValueError("Group name produces an invalid filename.")
    return name



def export_group(page, group):
    group_name = group["name"]
    from_date = date.fromisoformat(group["from_date"])
    to_date = date.today()

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


with sync_playwright() as p:
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
                export_group(page, group)
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

    finally:
        context.close()
