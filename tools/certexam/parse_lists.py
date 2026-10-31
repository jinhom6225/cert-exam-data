"""15003024 item list -> jm_list entries; 15088943 -> always-held names."""

import re
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional

from .grades import derive_grade
from .textutil import clean_text

_JM_CD = re.compile(r"\d{4}")

JmEntry = Dict[str, Optional[str]]


def parse_jm_list(rows: Iterable[Mapping[str, str]]) -> List[JmEntry]:
    """National technical qualifications (qualgbcd T) sorted by jmCd."""
    entries: Dict[str, JmEntry] = {}
    for row in rows:
        if (row.get("qualgbcd") or "").strip() != "T":
            continue
        jm_cd = (row.get("jmcd") or "").strip()
        name = clean_text(row.get("jmfldnm"))
        if not _JM_CD.fullmatch(jm_cd) or not name:
            continue
        entries[jm_cd] = {
            "jmCd": jm_cd,
            "name": name,
            "grade": derive_grade(name, row.get("seriescd")),
            "field": clean_text(row.get("obligfldnm")),
            "subField": clean_text(row.get("mdobligfldnm")),
        }
    return [entries[key] for key in sorted(entries)]


def parse_always_names(rows: Iterable[Mapping[str, str]]) -> FrozenSet[str]:
    """Item names listed by 15088943 (names only, a hint for regularOnly)."""
    names = (clean_text(row.get("jmnm")) for row in rows)
    return frozenset(name for name in names if name)
