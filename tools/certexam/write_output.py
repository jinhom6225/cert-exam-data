"""Deterministic exams.json writer (plan D24 ⑥⑦).

Keys are sorted, items are in jmCd order and rounds in (year, seq) order.
Each item sits on its own line so daily git diffs stay readable. The file
is rewritten only when something other than generatedAt changed.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .constants import MAX_OUTPUT_BYTES
from .errors import ValidationFailed

Document = Dict[str, object]


def format_generated_at(now_utc: datetime) -> str:
    return now_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sorted_items(doc: Document) -> list:
    items = sorted(doc.get("items") or [], key=lambda item: item["jmCd"])
    return [
        {**item, "rounds": sorted(item["rounds"], key=lambda r: (r["year"], r["seq"]))}
        for item in items
    ]


def render(doc: Document) -> bytes:
    parts = []
    for key in sorted(doc):
        if key == "items":
            lines = ",\n".join(_dump(item) for item in _sorted_items(doc))
            value = "[\n" + lines + "\n]" if lines else "[]"
        else:
            value = _dump(doc[key])
        parts.append(f"{_dump(key)}:{value}")
    return ("{" + ",\n".join(parts) + "}\n").encode("utf-8")


def without_generated_at(doc: Document) -> Document:
    return {key: value for key, value in doc.items() if key != "generatedAt"}


def load_previous(path: Path) -> Optional[Document]:
    """Previous exams.json, or None when there is none yet.

    An unreadable previous file is a validation failure: carrying on would
    throw away the item rounds collected on the other six days."""
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ValidationFailed("previous exams.json unreadable") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise ValidationFailed("previous exams.json has no items list")
    return doc


def is_unchanged(doc: Document, previous: Optional[Document]) -> bool:
    if previous is None:
        return False
    return render(without_generated_at(doc)) == render(without_generated_at(previous))


@dataclass(frozen=True)
class WriteResult:
    written: bool
    size: int


def write_if_changed(path: Path, doc: Document, previous: Optional[Document]) -> WriteResult:
    """Write atomically when content changed; report whether and how big."""
    data = render(doc)
    if len(data) > MAX_OUTPUT_BYTES:
        raise ValidationFailed(f"output over {MAX_OUTPUT_BYTES} bytes")
    if is_unchanged(doc, previous):
        return WriteResult(written=False, size=len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".exams.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as temp:
            temp.write(data)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise
    return WriteResult(written=True, size=len(data))
