import xml.etree.ElementTree as ET

import pytest

from src.sms_payload import (
    MAX_BODY_CHARS,
    PayloadError,
    SmsMessage,
    build_payload,
    parse_response,
    sanitize_body,
)


def msg(**overrides):
    kwargs = {
        "to": "+15125550143",
        "sender_id": "SMARTTAX",
        "body": "Your appointment is confirmed for Thursday at 2:00 PM.",
    }
    kwargs.update(overrides)
    return SmsMessage(**kwargs)


def test_ampersand_survives_serialization():
    """The outage. A caller said 'Smith & Sons' and the gateway silently
    stopped delivering because the XML was malformed."""
    payload = build_payload(msg(body="Booking for Smith & Sons is confirmed."))
    parsed = ET.fromstring(payload)
    assert parsed.findtext("text") == "Booking for Smith & Sons is confirmed."


def test_angle_brackets_and_quotes_survive():
    body = 'Re: <urgent> filing "Q3" deadline'
    parsed = ET.fromstring(build_payload(msg(body=body)))
    assert parsed.findtext("text") == body


def test_payload_has_the_fields_the_gateway_expects():
    parsed = ET.fromstring(build_payload(msg()))
    assert parsed.tag == "message"
    assert parsed.findtext("to") == "+15125550143"
    assert parsed.findtext("from") == "SMARTTAX"


def test_control_characters_are_stripped():
    assert sanitize_body("confirmed\x07 for Thursday") == "confirmed for Thursday"


def test_newlines_are_normalized():
    assert sanitize_body("line one\r\nline two\rline three") == (
        "line one\nline two\nline three"
    )


@pytest.mark.parametrize(
    "number",
    ["", "not-a-number", "555", "+1512555014312345678", "+0125550143"],
)
def test_bad_destination_is_rejected(number):
    with pytest.raises(PayloadError):
        msg(to=number)


def test_empty_body_is_rejected():
    with pytest.raises(PayloadError):
        msg(body="   ")


def test_oversized_body_is_rejected():
    with pytest.raises(PayloadError):
        msg(body="x" * (MAX_BODY_CHARS + 1))


def test_body_at_the_limit_is_allowed():
    assert msg(body="x" * MAX_BODY_CHARS).body


def test_missing_sender_is_rejected():
    with pytest.raises(PayloadError):
        msg(sender_id="")


def test_success_response_is_read_from_the_body():
    ok, detail = parse_response("<response><status>queued</status></response>")
    assert ok is True
    assert detail == "queued"


def test_error_body_behind_a_200_is_caught():
    """The gateway answers 200 even when it refuses the message. Only
    the body tells the truth."""
    ok, detail = parse_response(
        "<response><status>error</status><detail>invalid sender id</detail></response>"
    )
    assert ok is False
    assert detail == "invalid sender id"


def test_unparseable_response_is_a_failure_not_an_exception():
    ok, detail = parse_response("<response><status>ok</respon")
    assert ok is False
    assert "unparseable" in detail
