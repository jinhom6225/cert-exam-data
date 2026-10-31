"""Text and date normalisation shared by the parsers."""

import re
import unicodedata
from datetime import date
from typing import Mapping, Optional

_DATE8 = re.compile(r"\d{8}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SPACES = re.compile(r"\s+")


def clean_text(value: Optional[str]) -> Optional[str]:
    """Drop control/invisible characters, collapse spaces; blank -> None."""
    if value is None:
        return None
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in value
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    text = _SPACES.sub(" ", kept).strip()
    return text or None


def normalize_date(value: Optional[str]) -> Optional[str]:
    """`YYYYMMDD` -> `YYYY-MM-DD`; `XXXXXXXX`, blank or impossible -> None."""
    text = (value or "").strip()
    if not _DATE8.fullmatch(text):
        return None
    try:
        parsed = date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None
    return parsed.isoformat()


def is_iso_date(value: object) -> bool:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def first_value(row: Mapping[str, str], *keys: str) -> Optional[str]:
    """Return the first present value among lower-cased field names."""
    for key in keys:
        value = row.get(key)
        if value is not None and value.strip():
            return value
    return None
