# WhatsApp Chat Summarizer 

**Introduction:**  
A simple tool to summarize your WhatsApp chats using local AI. 

**What it does (step by step):**
1. **Export** - Get your chats as `.txt` or `.zip` (manually from WhatsApp, or optionally with `get-whatsapp-chat.py`).
2. **Parse** - Reads `.txt` or `.zip` WhatsApp exports.
3. **Summarize** - Uses a local Ollama model to create short summaries.
4. **Digest** - `main.py` builds a clean `digest_YYYY-MM-DD.md` file for you.

**How it's different:**
- **Runs without internet** - All AI runs on your machine.
- **Private** - Your messages & files stay on your host machine.
- **Setup once** - Do it once, use forever.
- **Never pay for it** - Free, uses your own computer.

> ⚠️ **Optional: Automatic Export (Use at your own risk)**  
> This feature uses browser automation on WhatsApp Web to export chats automatically.  
> This violates WhatsApp's Terms of Service and carries a small risk of account suspension.  
> Use a secondary number if you want to try it. The main app works perfectly without this feature using manual exports.

**I explored full automation but chose to make it opt-in due to ToS concerns — privacy and safety first.**

## Quickstart (do each step one by one)

### 1) Install required tools
- **Python**: [python.org/downloads](https://www.python.org/downloads/) (3.10+)
- **Ollama**: [ollama.com/download](https://ollama.com/download)
- **Git** (to clone repo): [git-scm.com/downloads](https://git-scm.com/downloads)

### 2) Download this repo
Open a terminal in the folder where you want this project.

```bash
git clone https://github.com/aadityasingh9601/Chat-summarizer.git
cd Chat-summarizer
```

### 3) Pull the AI model
```bash
ollama pull gemma4:e4b
```

### 4) Create a virtual environment & install requirements
**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium # Only needed if using Option B auto-export.
```

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium  # Only needed if using Option B auto-export.
```

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium  # Only needed if using Option B auto-export.
```

### 5) Get your WhatsApp chats
**Option A: Manual (Recommended, safe)**
1. Export chats from WhatsApp (Save as `.txt` or get `.zip` from WhatsApp export).
2. Put those `.txt`/`.zip` files in a folder (e.g. `./exports`).

**Option B: Auto-export (Optional, risky)**
1. Open `get-whatsapp-chat.py` and edit the `GROUPS` list. Add each group with `"name": "Exact Group Name"` and `"from_date": "YYYY-MM-DD"`.  
   - `from_date` is **required for the first run only** (used to set the starting point).  
   - After the first successful export, the script saves progress in `.whatsapp_export_state.json` (local only). Next runs will auto-start from **yesterday+1** so only new messages are exported up to **today**.  
   - If there's nothing new (already up to date), it will skip that group.  
   - `to_date` is always **today**. Use the exact group name as shown in WhatsApp Web.
2. Run it:
```bash
python get-whatsapp-chat.py
```
   - **First time:** A browser window will open to WhatsApp Web. Scan the QR code with your phone to log in. The script will continue once logged in.
*(Only do this if you understand the ToS risk above.)*

### 6) Create `.chat_digest_config.json` locally
Create this file in the `chat/` folder (`.chat_digest_config.json`). **This file is local only and never pushed to Git.**

```json
{
  "_comment": "Edit manually. Leave empty to be asked each time. This is local only.",
  "model": "gemma4:e4b",
  "input_folder": "./exports",
  "output_folder": "./summaries"
}
```

**Note:** `gemma4:e4b` is the default. For lower-end machines, you can also use `qwen3:4b`, `qwen3:1.7b`, or `qwen3:0.6b` instead. Pull them with `ollama pull <model>` and set in `.chat_digest_config.json`.

### 7) Run the summarizer
Make sure Ollama is running, your venv is activated, and you're inside the `chat/` folder.

```bash
python main.py
```

**Try it yourself:** A `sample_chats/` folder is included. When prompted for input folder, point to `./sample_chats` to test quickly without exporting your own chats.

Follow the prompts. You'll get `digest_YYYY-MM-DD.md` in your output folder.

## Schedule it to run every day (Cron / Task Scheduler)

You can run this automatically at the same time daily. Use the built-in scheduler for your OS.

### Linux / macOS (cron)
1. Make sure venv & files are in place. Find full paths:
```bash
pwd
which python3
```

2. Edit crontab:
```bash
crontab -e
```

3. Add this line (runs at 9 PM every day). Change paths/time as needed.
```bash
0 21 * * * cd /full/path/to/chat && /full/path/to/chat/venv/bin/python main.py >> /full/path/to/chat/cron.log 2>&1
```
**Note:** Put model, input folder and output folder in `.chat_digest_config.json`. In non-TTY (cron) `main.py` will use those defaults automatically (no Enter needed). If using Ollama locally, ensure Ollama is running or set it to auto-start.

### Windows (Task Scheduler)
1. Open **Task Scheduler** → **Create Basic Task**
2. Name: `WhatsApp Summarizer` → Trigger: **Daily** → Set time
3. Action: **Start a program**
   - **Program/script:** `C:\full\path\to\chat\venv\Scripts\python.exe`
   - **Add arguments:** `main.py`
   - **Start in:** `C:\full\path\to\chat\`
4. Save. Windows will run it at that time.

**Tip:** For fully unattended runs, use saved defaults in `.chat_digest_config.json` so prompts don't block. `main.py` auto-runs in non-TTY mode when defaults are present.
