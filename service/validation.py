"""Strict output validation against data/schema.json (Task 3).

The service never returns a body that is not exactly a four-key JSON object.
If the model wraps the object in prose we can still recover the first valid
object, but anything that fails the schema is a hard error.
"""

from __future__ import annotations

import json
from typing import Any


def validate_object(obj: Any, schema: dict) -> dict | None:
    """Return a normalised four-key dict, or None if ``obj`` violates the schema."""
    if not isinstance(obj, dict):
        return None
    keys = schema["keys"]
    if set(obj.keys()) != set(keys):
        return None
    intents = set(schema["intents"])
    intent = obj.get("intent")
    if not isinstance(intent, str) or intent not in intents:
        return None
    normalised: dict[str, Any] = {"intent": intent}
    for slot in ("date", "time", "place"):
        value = obj.get(slot)
        if value is not None and not isinstance(value, str):
            return None
        normalised[slot] = value
    return normalised


def parse_exact(raw: str, schema: dict) -> dict | None:
    try:
        obj = json.loads(raw.strip())
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    return validate_object(obj, schema)


def extract_first_valid(raw: str, schema: dict) -> dict | None:
    """Try the exact output first, then the first balanced JSON object.

    This only rescues objects that are themselves fully valid; it never
    repairs or rewrites a malformed object.
    """
    exact = parse_exact(raw, schema)
    if exact is not None:
        return exact
    if not isinstance(raw, str):
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        validated = validate_object(obj, schema)
        if validated is not None:
            return validated
    return None
