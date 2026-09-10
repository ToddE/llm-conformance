"""A small, strict JSON Schema validator.

Scope is deliberately narrow: it validates the subset WE use in our own probe
schemas. It is not a general-purpose validator and shouldn't grow into one.

Why hand-rolled instead of `jsonschema` from PyPI: the zero-dependency
guarantee is load-bearing for reproduction. A third party disputing a finding
should not have to install anything to re-run it. The subset below is small
enough to audit by reading it, which is a stronger guarantee than a large
dependency anyway.

Every violation is reported as a human-readable path string PREFIXED with the
schema keyword that produced it (`[enum] $.verdict: ...`). The prefix exists so
violations can be tiered by enforceability -- see KEYWORD_TIER below -- which
is what keeps advisory-keyword leakage out of the silent-failure rate.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a list of violations. Empty list means valid."""
    errors: list[str] = []
    expected = schema.get("type")

    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(instance, t) for t in types):
            return [f"[type] {path}: expected type {'|'.join(types)}, got {_type_name(instance)}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"[enum] {path}: value {instance!r} not in enum {schema['enum']!r}")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"[const] {path}: value {instance!r} != const {schema['const']!r}")

    if isinstance(instance, dict) and (expected == "object" or "properties" in schema):
        errors += _validate_object(instance, schema, path)

    if isinstance(instance, list) and (expected == "array" or "items" in schema):
        errors += _validate_array(instance, schema, path)

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"[minLength] {path}: string shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"[maxLength] {path}: string longer than maxLength {schema['maxLength']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"[minimum] {path}: {instance} below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"[maximum] {path}: {instance} above maximum {schema['maximum']}")

    return errors


def _validate_object(instance: dict, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    props: dict[str, Any] = schema.get("properties", {})

    for key in schema.get("required", []):
        if key not in instance:
            errors.append(f"[required] {path}.{key}: required property missing")

    # additionalProperties: false is the single most useful assertion in this
    # whole project. "Did the model invent a field" is the most common way a
    # strict-mode claim turns out to be false in practice.
    if schema.get("additionalProperties") is False:
        for key in instance:
            if key not in props:
                errors.append(
                    f"[additionalProperties] {path}.{key}: additional property "
                    f"not permitted (additionalProperties=false)"
                )

    for key, subschema in props.items():
        if key in instance:
            errors += validate(instance[key], subschema, f"{path}.{key}")

    return errors


def _validate_array(instance: list, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if "minItems" in schema and len(instance) < schema["minItems"]:
        errors.append(f"[minItems] {path}: {len(instance)} items, below minItems {schema['minItems']}")
    if "maxItems" in schema and len(instance) > schema["maxItems"]:
        errors.append(f"[maxItems] {path}: {len(instance)} items, above maxItems {schema['maxItems']}")
    items = schema.get("items")
    if isinstance(items, dict):
        for i, item in enumerate(instance):
            errors += validate(item, items, f"{path}[{i}]")
    return errors


def _is_type(instance: Any, type_name: str) -> bool:
    expected = _TYPES.get(type_name)
    if expected is None:
        return True  # unknown keyword: don't invent a failure
    # JSON has no bool/int distinction in Python's type system, but a schema
    # that says "integer" and receives `true` is a real violation.
    if type_name in ("integer", "number") and isinstance(instance, bool):
        return False
    if type_name == "integer" and isinstance(instance, float):
        return instance.is_integer()
    return isinstance(instance, expected)


def _type_name(instance: Any) -> str:
    if instance is None:
        return "null"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, dict):
        return "object"
    return type(instance).__name__


# ── Keyword enforceability tiers ─────────────────────────────────────────────
#
# Not every JSON Schema keyword is part of constrained decoding. This matters
# enormously for scoring.
#
# STRUCTURAL keywords are what a strict decoder actually constrains: the shape
# and type of the output. If one of these leaks, the endpoint genuinely failed
# to enforce a constraint it accepted.
#
# ADVISORY keywords are widely NOT enforced by strict decoders and are treated
# as hints to the model. OpenAI's strict mode rejects `maxLength` outright as
# an unsupported keyword; Anthropic's tool `input_schema` accepts it and does
# not constrain on it.
#
# Scoring an advisory-keyword leak as a silent failure would be a FALSE
# POSITIVE -- refutable by a provider in one sentence, and fatal to the
# credibility of every other finding in the report. So advisory violations get
# their own outcome and are excluded from the headline silent-failure rate.
#
# This choice deliberately LOWERS our own divergence number. That is the
# correct direction for a measurement project to be wrong in.

STRUCTURAL_KEYWORDS = {
    "type", "enum", "const", "required", "additionalProperties",
}

ADVISORY_KEYWORDS = {
    "maxLength", "minLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minItems", "maxItems", "multipleOf", "uniqueItems",
}


def violation_keyword(violation: str) -> str:
    """Extract the schema keyword from a violation string."""
    if violation.startswith("["):
        return violation[1:violation.index("]")] if "]" in violation else "unknown"
    return "unknown"


def tier(violation: str) -> str:
    kw = violation_keyword(violation)
    if kw in STRUCTURAL_KEYWORDS:
        return "structural"
    if kw in ADVISORY_KEYWORDS:
        return "advisory"
    return "unknown"


def all_advisory(violations: list[str]) -> bool:
    """True if every violation is an advisory-keyword leak (and there is one)."""
    real = [v for v in violations if not v.startswith("dropped_from_request")]
    return bool(real) and all(tier(v) == "advisory" for v in real)
