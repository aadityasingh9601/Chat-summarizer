
from datetime import date
from pathlib import Path
import re

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ---------- Configuration ----------

GROUP_NAME = "Aryan Kushwaha"
FROM_DATE = "2026-10-10"  # YYYY-MM-DD

SCRIPT_DIR = Path(__file__).resolve().parent
EXPORT_DIR = SCRIPT_DIR / "exports"
PROFILE_DIR = SCRIPT_DIR / "whatsapp-profile"

EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# Make the filename safe for Windows/Linux.
safe_group_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", GROUP_NAME).strip(" .")
if not safe_group_name:
    raise ValueError("GROUP_NAME must produce a valid filename.")

OUTPUT_FILE = EXPORT_DIR / f"{safe_group_name}.zip"


# ---------- Automation ----------

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        accept_downloads=True,
    )

    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

        # Wait for WhatsApp to load instead of relying on a fixed sleep.
        search_box = page.get_by_role(
            "textbox",
            name="Search or start a new chat",
            exact=True,
        )
        search_box.wait_for(state="visible", timeout=60000)

        # Search for the group.
        search_box.fill(GROUP_NAME)

        group_result = page.get_by_text(GROUP_NAME, exact=True)
        group_result.first.wait_for(state="visible", timeout=15000)

        print(f"Opening group: {GROUP_NAME}")
        group_result.first.click()

        # Open the menu in the conversation header.
        chat_header = page.get_by_test_id("conversation-header")
        menu_button = chat_header.get_by_role(
            "button",
            name="Menu",
            exact=True,
        )
        menu_button.click()

        # Choose Export chat.
        page.get_by_text("Export chat", exact=True).click()

        dialog = page.get_by_role("dialog")
        dialog.wait_for(state="visible", timeout=10000)

        # Select a custom date range.
        dialog.get_by_text("Custom date range", exact=True).click()

        to_date = date.today().isoformat()

        # Fill the date inputs.
        dialog.get_by_label("Start date").fill(FROM_DATE)
        dialog.get_by_label("End date").fill(to_date)

        print(f"Exporting from {FROM_DATE} to {to_date}")

        # The download listener MUST be active before clicking Export.
        with page.expect_download(timeout=60000) as download_info:
            dialog.get_by_role(
                "button",
                name="Export",
                exact=True,
            ).click()

        download = download_info.value

        # Save directly to your computer with the group name.
        download.save_as(str(OUTPUT_FILE))

        print(f"Export completed: {OUTPUT_FILE}")

    except PlaywrightTimeoutError as exc:
        print(f"Timed out waiting for WhatsApp or the export: {exc}")
        raise

    finally:
        context.close()
-