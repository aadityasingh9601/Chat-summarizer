# WhatsApp Chat Summarizer 

**Introduction:**  
A simple tool to summarize your WhatsApp chats using local AI. 

**What it does (step by step):**
1. **Auto-export** - `get-whatsapp-chat.py` pulls your chats off WhatsApp Web.
2. **Parse** - Reads `.txt` or `.zip` WhatsApp exports.
3. **Summarize** - Uses a local Ollama model to create short summaries.
4. **Digest** - `main.py` builds a clean `digest_YYYY-MM-DD.md` file for you.

**How it's different:**
- **Runs without internet** - All AI runs on your machine.
- **Private** - Your messages & files stay on your host machine.
- **Setup once** - Do it once, use forever.
- **Never pay for it** - Free, uses your own computer.

## Quickstart (even if you're not technical)

### 1) Install
- **Python**: [python.org](https://www.python.org/downloads/) (3.10+)
- **Ollama**: [ollama.com/download](https://ollama.com/download)

### 2) Pull AI model
```bash
ollama pull qwen3:1.7b
```

### 3) Create venv & install
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate
pip install -r requirements.txt
```

### 4) Auto-export WhatsApp chats
```bash
python get-whatsapp-chat.py
```
*(Opens WhatsApp Web and exports chats to a folder)*

### 5) Create config (optional but helpful)
Create a file named `.chat_digest_config.json` in this folder with:
```json
{
  "_comment": "Edit manually. Leave empty to be asked each time.",
  "model": "qwen3:1.7b",
  "input_folder": "./exports",
  "output_folder": "./summaries"
}
```
**Purpose:** Saves your default model + input/output folders so `main.py` doesn't ask every time.

### 6) Run & get summaries
```bash
python main.py
```
Follow the prompts. You'll get a clean digest in your output folder.