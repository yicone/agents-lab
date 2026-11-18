"""Dependency-free validator for the keywords in the approved v1 schemas.

Schema files originate from the approved design at 482432f; snapshot adds optional
domain collection evidence for ACM-927. Unsupported
assertion keywords fail closed; this is deliberately not a general JSON Schema
implementation. Errors never include input values, keys or paths.
"""
import json
import re
from datetime import datetime
from pathlib import Path


class InvalidDocument(ValueError):
    def __init__(self):
        super().__init__("invalid_document")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise InvalidDocument() from None


def load_schema(name):
    path = Path(__file__).resolve().parents[1] / "schemas" / ("box-config-" + name + ".schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


_TYPES = {
    "object": lambda v: type(v) is dict,
    "array": lambda v: type(v) is list,
    "string": lambda v: type(v) is str,
    "integer": lambda v: type(v) is int,
    "number": lambda v: type(v) in (int, float),
    "boolean": lambda v: type(v) is bool,
    "null": lambda v: v is None,
}
_KEYS = {"$schema", "$id", "$defs", "$ref", "title", "description", "type",
         "const", "enum", "required", "properties", "additionalProperties",
         "minProperties", "maxProperties", "items", "minItems", "maxItems",
         "uniqueItems", "minimum", "maximum", "minLength", "maxLength",
         "pattern", "format", "oneOf", "allOf", "if", "then", "else", "not"}


def validate(value, schema, root=None):
    root = schema if root is None else root
    if set(schema) - _KEYS:
        raise InvalidDocument()
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise InvalidDocument()
        validate(value, root["$defs"][ref.split("/")[-1]], root)
    if "type" in schema:
        types = schema["type"] if type(schema["type"]) is list else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            raise InvalidDocument()
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise InvalidDocument()
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise InvalidDocument()
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate(value, branch, root)
                matches += 1
            except InvalidDocument:
                pass
        if matches != 1:
            raise InvalidDocument()
    for branch in schema.get("allOf", []):
        validate(value, branch, root)
    if "if" in schema:
        try:
            validate(value, schema["if"], root)
            branch = "then"
        except InvalidDocument:
            branch = "else"
        validate(value, schema.get(branch, {}), root)
    if "not" in schema:
        try:
            validate(value, schema["not"], root)
        except InvalidDocument:
            pass
        else:
            raise InvalidDocument()
    if type(value) is dict:
        if not set(schema.get("required", [])).issubset(value):
            raise InvalidDocument()
        if not schema.get("minProperties", 0) <= len(value) <= schema.get("maxProperties", float("inf")):
            raise InvalidDocument()
        props = schema.get("properties", {})
        for key, item in value.items():
            rule = props.get(key, schema.get("additionalProperties", {}))
            if rule is False:
                raise InvalidDocument()
            if rule is not True:
                validate(item, rule, root)
    if type(value) is list:
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf")):
            raise InvalidDocument()
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise InvalidDocument()
        for item in value:
            validate(item, schema.get("items", {}), root)
    if type(value) is str:
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
            raise InvalidDocument()
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise InvalidDocument()
        if schema.get("format") == "date-time":
            timestamp(value)
    if type(value) in (int, float):
        if not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
            raise InvalidDocument()


def parse_json(data):
    """Reject duplicate keys, nonfinite numbers and over-deep JSON."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidDocument()
            result[key] = value
        return result

    def reject(_):
        raise InvalidDocument()

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidDocument() from None
