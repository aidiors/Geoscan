"""Stable project checksums across Python and browser JSON serialization."""

import hashlib
import json
from math import isfinite

from pydantic import JsonValue

from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError


def canonical_json(value: JsonValue) -> bytes:
    def normalize(item: JsonValue) -> JsonValue:
        if isinstance(item, float):
            if not isfinite(item):
                raise PlanningError(
                    ErrorCode.PROJECT_INVALID, "Проект содержит некорректное число."
                )
            return int(item) if item.is_integer() else item
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        return item

    return json.dumps(
        normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def document_digest(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
