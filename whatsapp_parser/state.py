from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _get_state_path() -> Path:
    return Path(__file__).parent.parent / ".chat_summary_state.json"


def load_last_summary(source: str) -> datetime | None:
    path = _get_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data.get(source)
        if not ts:
            return None
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def save_last_summary(source: str, when: datetime | None = None) -> None:
    if when is None:
        when = datetime.now()
    path = _get_state_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[source] = when.isoformat()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
