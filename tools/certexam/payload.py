"""Safe decoding of API bodies into rows with lower-cased field names."""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .errors import CallFailed

_UNSAFE_XML = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_SAFE_CODE = re.compile(r"[A-Za-z0-9_]{1,12}")

Row = Dict[str, str]


@dataclass(frozen=True)
class Payload:
    rows: Tuple[Row, ...]
    total_count: Optional[int]


def _safe_code(raw: Optional[str]) -> str:
    text = (raw or "").strip()
    return text if _SAFE_CODE.fullmatch(text) else "?"


def _to_int(raw: object) -> Optional[int]:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def decode(operation: str, body: bytes) -> Payload:
    """Decode an XML or JSON body; raise CallFailed unless resultCode is 00."""
    head = body.lstrip()[:1]
    if head in (b"{", b"["):
        return _decode_json(operation, body)
    return _decode_xml(operation, body)


def _decode_xml(operation: str, body: bytes) -> Payload:
    """Strict UTF-8 only: a UTF-16 body could hide a DOCTYPE from a byte
    search. The DOCTYPE/ENTITY and NUL checks run on the decoded text and
    the parser gets that same text."""
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise CallFailed(operation, "XML not UTF-8", retryable=False) from None
    if "\x00" in text or _UNSAFE_XML.search(text):
        raise CallFailed(operation, "unsafe XML (DOCTYPE/ENTITY/NUL)", retryable=False)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise CallFailed(operation, "malformed XML") from None
    if root.tag == "OpenAPI_ServiceResponse":
        code = root.findtext(".//returnReasonCode")
        raise CallFailed(operation, "gateway error", result_code=_safe_code(code))
    code = root.findtext("./header/resultCode")
    if code is None or code.strip() != "00":
        raise CallFailed(operation, "result not OK", result_code=_safe_code(code))
    rows = tuple(
        {child.tag.lower(): (child.text or "") for child in item}
        for item in root.iter("item")
    )
    return Payload(rows=rows, total_count=_to_int(root.findtext("./body/totalCount")))


def _json_items(body_obj: object) -> List[object]:
    if not isinstance(body_obj, dict):
        return []
    items = body_obj.get("items")
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else []


def _decode_json(operation: str, body: bytes) -> Payload:
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CallFailed(operation, "malformed JSON") from None
    if not isinstance(doc, dict):
        raise CallFailed(operation, "malformed JSON")
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    code = header.get("resultCode")
    if code is None or str(code).strip() != "00":
        raise CallFailed(operation, "result not OK", result_code=_safe_code(str(code)))
    body_obj = doc.get("body")
    rows = tuple(
        {str(k).lower(): "" if v is None else str(v) for k, v in item.items()}
        for item in _json_items(body_obj)
        if isinstance(item, dict)
    )
    total = body_obj.get("totalCount") if isinstance(body_obj, dict) else None
    return Payload(rows=rows, total_count=_to_int(total))
