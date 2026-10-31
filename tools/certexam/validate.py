"""Validation with the same rules as the app (research §3.4, plan D10/D11).

Bad rounds and items are dropped; the whole document is rejected when the
dropped items pass 20 percent or 100, or a document-level rule fails.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import (
    GRADES,
    LIMIT_FIELD,
    LIMIT_GRADE,
    LIMIT_ITEMS,
    LIMIT_LABEL,
    LIMIT_NAME,
    LIMIT_ROUNDS_PER_ITEM,
    LIMIT_SOURCE_LABEL,
    MAX_DROPPED_ITEM_PERCENT,
    MAX_DROPPED_ITEMS,
    SCHEMA_VERSION,
)
from .errors import ValidationFailed
from .textutil import clean_text, is_iso_date

_JM_CD = re.compile(r"\d{4}")
_GENERATED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

PHASES: Dict[str, Tuple[Tuple[str, ...], Tuple[Tuple[str, str], ...]]] = {
    "written": (
        ("regStart", "regEnd", "examStart", "examEnd", "pass"),
        (("regStart", "regEnd"), ("examStart", "examEnd")),
    ),
    "docSubmit": (("start", "end"), (("start", "end"),)),
    "practical": (
        ("regStart", "regEnd", "examStart", "examEnd", "passStart", "passEnd"),
        (("regStart", "regEnd"), ("examStart", "examEnd"), ("passStart", "passEnd")),
    ),
}


@dataclass(frozen=True)
class ValidationReport:
    document: Dict[str, object]
    dropped_items: Tuple[str, ...]
    dropped_rounds: int
    restored_items: Tuple[str, ...] = ()


def text_ok(value: object, limit: int, required: bool) -> bool:
    if value is None:
        return not required
    if not isinstance(value, str) or clean_text(value) != value:
        return False
    return 0 < len(value) <= limit


def _is_int(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def phase_ok(name: str, value: object) -> bool:
    if value is None:
        return True
    fields, pairs = PHASES[name]
    if not isinstance(value, dict) or set(value) != set(fields):
        return False
    if any(v is not None and not is_iso_date(v) for v in value.values()):
        return False
    return all(
        value[start] is None or value[end] is None or value[start] <= value[end]
        for start, end in pairs
    )


def round_ok(round_: object) -> bool:
    if not isinstance(round_, dict):
        return False
    phases_present = [round_.get(name) is not None for name in PHASES]
    return (
        _is_int(round_.get("year"), 2000, 2100)
        and _is_int(round_.get("seq"), 1, 999)
        and text_ok(round_.get("label"), LIMIT_LABEL, required=True)
        and round_.get("basis") in ("item", "grade")
        and all(phase_ok(name, round_.get(name)) for name in PHASES)
        and any(phases_present)
    )


def fee_ok(fee: object) -> bool:
    if fee is None:
        return True
    return (
        isinstance(fee, dict)
        and set(fee) == {"written", "practical"}
        and all(_is_int(v, 0, 10_000_000) for v in fee.values())
    )


def check_item(item: object) -> Tuple[Optional[Dict[str, object]], int]:
    """Return (item with bad rounds removed or None, dropped round count)."""
    if not isinstance(item, dict):
        return None, 0
    rounds = item.get("rounds")
    basic = (
        isinstance(item.get("jmCd"), str)
        and _JM_CD.fullmatch(item["jmCd"]) is not None
        and text_ok(item.get("name"), LIMIT_NAME, required=True)
        and text_ok(item.get("grade"), LIMIT_GRADE, required=True)
        and item.get("grade") in GRADES
        and text_ok(item.get("field"), LIMIT_FIELD, required=False)
        and text_ok(item.get("subField"), LIMIT_FIELD, required=False)
        and fee_ok(item.get("fee"))
        and item.get("regularOnly") in (True, False, None)
        and isinstance(rounds, list)
    )
    if not basic:
        return None, 0
    kept = [r for r in rounds if round_ok(r)]
    keys = [(r["year"], r["seq"]) for r in kept]
    if len(kept) > LIMIT_ROUNDS_PER_ITEM or len(set(keys)) != len(keys):
        return None, len(rounds)
    return {**item, "rounds": kept}, len(rounds) - len(kept)


def _check_document_fields(doc: Dict[str, object]) -> None:
    source = doc.get("source")
    if doc.get("schemaVersion") != SCHEMA_VERSION:
        raise ValidationFailed("schemaVersion")
    if not isinstance(doc.get("generatedAt"), str) or not _GENERATED_AT.fullmatch(doc["generatedAt"]):
        raise ValidationFailed("generatedAt")
    if (
        not isinstance(source, dict)
        or not text_ok(source.get("label"), LIMIT_SOURCE_LABEL, required=True)
        or not str(source.get("url", "")).startswith("https://")
    ):
        raise ValidationFailed("source")
    items = doc.get("items")
    if not isinstance(items, list) or len(items) > LIMIT_ITEMS:
        raise ValidationFailed("items")


def validate_document(doc: Dict[str, object]) -> ValidationReport:
    _check_document_fields(doc)
    items: Sequence[object] = doc["items"]  # type: ignore[assignment]
    kept: List[Dict[str, object]] = []
    dropped: List[str] = []
    dropped_rounds = 0
    seen = set()
    for item in items:
        checked, bad_rounds = check_item(item)
        dropped_rounds += bad_rounds
        if checked is None or checked["jmCd"] in seen:
            dropped.append(str(item.get("jmCd") if isinstance(item, dict) else "?")[:8])
            continue
        seen.add(checked["jmCd"])
        kept.append(checked)
    total = len(items)
    if len(dropped) > MAX_DROPPED_ITEMS or len(dropped) * 100 > total * MAX_DROPPED_ITEM_PERCENT:
        raise ValidationFailed(f"too many invalid items ({len(dropped)} of {total})")
    return ValidationReport({**doc, "items": kept}, tuple(dropped), dropped_rounds)


def restore_previous(
    report: ValidationReport, previous_items: Mapping[str, object]
) -> ValidationReport:
    """Put back the previous valid record of each item dropped today, so a
    bad answer does not make the item vanish from the app."""
    kept = list(report.document["items"])  # type: ignore[arg-type]
    present = {item["jmCd"] for item in kept}
    restored: List[str] = []
    for jm_cd in report.dropped_items:
        if jm_cd in present or jm_cd not in previous_items:
            continue
        checked, _ = check_item(previous_items[jm_cd])
        if checked is None or checked["jmCd"] != jm_cd:
            continue
        kept.append(checked)
        present.add(jm_cd)
        restored.append(jm_cd)
    return ValidationReport(
        document={**report.document, "items": sorted(kept, key=lambda i: i["jmCd"])},
        dropped_items=report.dropped_items,
        dropped_rounds=report.dropped_rounds,
        restored_items=tuple(restored),
    )
