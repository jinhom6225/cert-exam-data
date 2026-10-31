"""getFeeList -> {"written": int, "practical": int} or None."""

import re
from typing import Dict, Iterable, Mapping, Optional

from .textutil import clean_text

_FEE = re.compile(r"1차\s*:\s*(\d{1,7})\s*,\s*2차\s*:\s*(\d{1,7})")
_FEE_KIND = "응시수수료"


def parse_fee(rows: Iterable[Mapping[str, str]]) -> Optional[Dict[str, int]]:
    """Parse `1차 : 19400, 2차 : 22600 `; any other shape -> None."""
    for row in rows:
        if clean_text(row.get("infogb")) not in (_FEE_KIND, None):
            continue
        text = clean_text(row.get("contents")) or ""
        match = _FEE.fullmatch(text)
        if match:
            return {"written": int(match.group(1)), "practical": int(match.group(2))}
        return None
    return None
