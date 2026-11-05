"""Strict JSON decoding and sanitized, machine-classified host failures."""

import json
import math


class HostFailure(ValueError):
    def __init__(self, message, kind=None):
        super().__init__(message)
        self.kind = kind


def loads(text):
    def invalid_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("JSON number exceeds finite range")
        return parsed

    def unique_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON contains duplicate object keys")
            result[key] = value
        return result

    return json.loads(text, parse_constant=invalid_constant, parse_float=finite_float,
                      object_pairs_hook=unique_object)


def events(text):
    output = []
    for line in text.splitlines():
        if line.strip():
            event = loads(line)
            if not isinstance(event, dict):
                raise ValueError("Host event is not a JSON object")
            output.append(event)
    return output


def money(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def failure_kind(code):
    """Classify explicit host codes only; never infer from potentially secret prose."""
    if not isinstance(code, str):
        return None
    code = code.lower()
    return {
        "invalid_api_key": "auth", "authentication_error": "auth",
        "unauthorized": "auth",
        "providerautherror": "auth", "insufficient_quota": "quota",
        "quota_exceeded": "quota", "error_max_budget_usd": "quota",
        "model_not_found": "config", "modelnotfounderror": "config",
        "providermodelnotfounderror": "config", "ratelimiterror": "transient",
        "rate_limit_exceeded": "transient", "rate_limit_error": "transient",
        "overloaded_error": "transient", "api_connection_error": "transient",
    }.get(code)
