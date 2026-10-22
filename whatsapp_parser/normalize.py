from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

INVISIBLE_CHARS = (
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u202a"
    "\u202b"
    "\u202c"
    "\u202d"
    "\u202e"
    "\u2060"
    "\u2061"
    "\u2062"
    "\u2063"
    "\u2064"
    "\u2066"
    "\u2067"
    "\u2068"
    "\u2069"
    "\ufeff"  # zero width no-break space / BOM
    "\u00ad"  # soft hyphen
    "\u0000"
    "\u200b"  # zero width space
)

_INVISIBLE_RE = re.compile("[" + re.escape(INVISIBLE_CHARS) + "]")
_SPACE_RE = re.compile("[\u00a0\u202f\u2007]")


def decode_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    sample = data[:4096]
    if sample.count(b"\x00") > len(sample) // 4:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def normalize_line(line: str) -> str:
    line = _INVISIBLE_RE.sub("", line)
    line = _SPACE_RE.sub(" ", line)
    return line.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(normalize_line(line) for line in text.split("\n"))


def _is_usable_entry(name: str) -> bool:
    name = name.replace("\\", "/")
    if name.startswith("__MACOSX/") or "/__MACOSX/" in name:
        return False
    if name.split("/")[-1].startswith("._"):
        return False
    return name.lower().endswith(".txt")


def _best_transcript_entry(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    candidates = [info for info in archive.infolist() if _is_usable_entry(info.filename)]
    if not candidates:
        raise ValueError("no .txt transcript found in the archive")
    return max(candidates, key=lambda info: info.file_size)


@contextmanager
def resolve_transcript(path: str | Path) -> Iterator[Path]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"no such file: {source}")
    if source.suffix.lower() != ".zip":
        yield source
        return
    with TemporaryDirectory() as tmp:
        with zipfile.ZipFile(source) as archive:
            info = _best_transcript_entry(archive)
            target = Path(tmp) / Path(info.filename).name
            with archive.open(info) as reader, target.open("wb") as writer:
                writer.write(reader.read())
        yield target


def sniff_encoding(path: str | Path) -> str:
    with Path(path).open("rb") as handle:
        head = handle.read(4096)
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    if head and head.count(b"\x00") > len(head) // 4:
        return "utf-16-le"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def iter_text_lines(path: str | Path) -> Iterator[str]:
    encoding = sniff_encoding(path)
    with Path(path).open("r", encoding=encoding, errors="replace", newline="") as handle:
        for line in handle:
            yield normalize_line(line)


def iter_lines(path: str | Path) -> Iterator[str]:
    with resolve_transcript(path) as transcript:
        yield from iter_text_lines(transcript)
