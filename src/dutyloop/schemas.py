"""Small, closed response contracts shared by the planner and host drivers."""

import re


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


DISCOVERY_SCHEMA = _object({
    "status": {"type": "string", "enum": ["work", "wait", "blocked"]},
    "summary": {"type": "string"},
    "candidates": {
        "type": "array", "maxItems": 3,
        "items": _object({
            "key": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
            "title": {"type": "string"},
            "kind": {"type": "string", "enum": ["feature", "bugfix", "qa", "docs"]},
            "why": {"type": "string"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
            "evidence": {"type": "array", "items": _object({
                "source": {"type": "string"}, "observation": {"type": "string"},
            })},
            "acceptance": {"type": "array", "minItems": 1,
                           "items": {"type": "string", "minLength": 1}},
        }),
    },
})

IMPLEMENT_SCHEMA = _object({
    "summary": {"type": "string"}, "completed": {"type": "boolean"},
})

REVIEW_SCHEMA = _object({
    "accepted": {"type": "boolean"}, "summary": {"type": "string"},
    "findings": {"type": "array", "items": {"type": "string"}},
})


def validate_payload(value, schema, path="$"):
    """Validate the JSON Schema subset used by DutyLoop's closed contracts.

    This is deliberately not a general JSON Schema implementation. Native hosts
    also receive the schema; custom command responses receive this same check.
    """
    kind = schema.get("type")
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }
    if kind and not matches.get(kind, False):
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is outside enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            raise ValueError(f"{path}: missing required properties")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{path}: unexpected properties")
        for key, child in value.items():
            if key in properties:
                validate_payload(child, properties[key], f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path}: array length is outside allowed range")
        for index, child in enumerate(value):
            if "items" in schema:
                validate_payload(child, schema["items"], f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: string is too short")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{path}: string does not match pattern")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise ValueError(f"{path}: number is outside allowed range")
