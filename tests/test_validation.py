from __future__ import annotations

import json

from service.validation import extract_first_valid, parse_exact, validate_object


def good():
    return {"intent": "weather_query", "date": None, "time": None, "place": "london"}


def test_exact_object_is_valid(schema):
    assert parse_exact(json.dumps(good()), schema) == good()


def test_extra_or_missing_keys_are_rejected(schema):
    extra = dict(good(), confidence=0.9)
    assert validate_object(extra, schema) is None
    missing = {"intent": "weather_query", "date": None, "time": None}
    assert validate_object(missing, schema) is None


def test_bad_intent_and_bad_slot_types_are_rejected(schema):
    assert validate_object(dict(good(), intent="chat"), schema) is None
    assert validate_object(dict(good(), date=20250101), schema) is None


def test_wrapped_object_is_extracted(schema):
    wrapped = 'Here you go:\n```json\n' + json.dumps(good()) + "\n```"
    assert extract_first_valid(wrapped, schema) == good()


def test_not_json_returns_none(schema):
    assert extract_first_valid("not json", schema) is None


def test_first_invalid_braces_do_not_stop_search(schema):
    text = 'noise {broken} then ' + json.dumps(good())
    assert extract_first_valid(text, schema) == good()
