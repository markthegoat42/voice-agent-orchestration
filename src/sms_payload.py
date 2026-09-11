"""
XML payload construction for the SMS gateway.

The gateway this was built against takes XML, not JSON. The first
version of this code built the document with string formatting, which
worked until a caller left a voicemail containing an ampersand. The
gateway returned 200 with an error body, so nothing looked broken until
someone noticed the texts had stopped arriving.

Two lessons are baked in below:

1. Build XML with a serializer, never with string concatenation. The
   escaping rules are not worth re-deriving and getting subtly wrong.
2. A 200 response is not a success. Parse the body and check the status
   the provider actually reports inside it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

# E.164-ish. Deliberately permissive on length, strict on shape.
_PHONE = re.compile(r"^\+?[1-9]\d{7,14}$")

# Characters the gateway rejects outright rather than escaping.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_BODY_CHARS = 918  # 6 concatenated GSM-7 segments


class PayloadError(ValueError):
    """Raised when a message cannot be represented for the gateway."""


@dataclass(frozen=True)
class SmsMessage:
    to: str
    sender_id: str
    body: str

    def __post_init__(self) -> None:
        if not _PHONE.match(self.to.strip()):
            raise PayloadError(f"destination is not a valid number: {self.to!r}")
        if not self.sender_id.strip():
            raise PayloadError("sender_id is required")
        if not self.body.strip():
            raise PayloadError("body is empty")
        if len(self.body) > MAX_BODY_CHARS:
            raise PayloadError(
                f"body is {len(self.body)} chars, limit is {MAX_BODY_CHARS}"
            )


def sanitize_body(text: str) -> str:
    """Strip characters that are illegal in XML 1.0 and collapse newlines.

    Escaping is handled by the serializer. This only removes what cannot
    be escaped at all, which is the control-character range.
    """
    cleaned = _CONTROL_CHARS.sub("", text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned.strip()


def build_payload(message: SmsMessage) -> bytes:
    """Serialize a message to the gateway's XML request format.

    ElementTree handles escaping, so `&`, `<`, `>` and quotes in caller
    input survive the trip intact.
    """
    root = ET.Element("message")
    ET.SubElement(root, "to").text = message.to.strip()
    ET.SubElement(root, "from").text = message.sender_id.strip()
    ET.SubElement(root, "text").text = sanitize_body(message.body)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_response(raw: bytes | str) -> tuple[bool, str]:
    """Read the gateway's response body.

    Returns (ok, detail). The transport status code is not consulted
    here on purpose: this gateway returns 200 for application-level
    failures, so the body is the only reliable signal.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return False, f"unparseable response from gateway: {exc}"

    status = (root.findtext("status") or "").strip().lower()
    detail = (root.findtext("detail") or root.findtext("error") or "").strip()

    if status in {"ok", "success", "queued", "sent"}:
        return True, detail or status
    return False, detail or f"gateway reported status {status!r}"
