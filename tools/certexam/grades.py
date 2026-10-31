"""Grade derivation from the item name and series code (data_spike section 1).

Series 03 mixes 기사, 산업기사 and service-field items, so the name decides:
strip parenthetical parts, then check "산업기사" before "기사".
"""

import re
from typing import Optional

_PARENS = re.compile(r"\([^)]*\)")

SERIES_DIRECT = {"01": "기술사", "02": "기능장", "04": "기능사"}

# Grade-level schedule groups: 기사, 산업기사 and 서비스 share getEList.
GROUP_PE = "PE"
GROUP_MC = "MC"
GROUP_E = "E"
GROUP_C = "C"

GRADE_GROUP = {
    "기술사": GROUP_PE,
    "기능장": GROUP_MC,
    "기사": GROUP_E,
    "산업기사": GROUP_E,
    "서비스": GROUP_E,
    "기능사": GROUP_C,
}

GROUP_LABEL = {GROUP_PE: "기술사", GROUP_MC: "기능장", GROUP_E: "기사", GROUP_C: "기능사"}


def _grade_from_name(base: str) -> str:
    for grade in ("기술사", "기능장", "산업기사", "기사", "기능사"):
        if grade in base:
            return grade
    return "서비스"


def derive_grade(name: str, series_cd: Optional[str]) -> str:
    """Return one of 기술사, 기능장, 기사, 산업기사, 기능사, 서비스."""
    direct = SERIES_DIRECT.get((series_cd or "").strip())
    if direct:
        return direct
    base = _PARENS.sub("", name).strip()
    if (series_cd or "").strip() == "03":
        if "산업기사" in base:
            return "산업기사"
        return "기사" if "기사" in base else "서비스"
    return _grade_from_name(base)


def group_of(grade: str) -> Optional[str]:
    return GRADE_GROUP.get(grade)


def group_from_text(text: str) -> Optional[str]:
    """Map a grade word in a schedule description to a group."""
    for word, group in (
        ("기술사", GROUP_PE),
        ("기능장", GROUP_MC),
        ("기능사", GROUP_C),
        ("산업기사", GROUP_E),
        ("기사", GROUP_E),
        ("서비스", GROUP_E),
    ):
        if word in text:
            return group
    return None
