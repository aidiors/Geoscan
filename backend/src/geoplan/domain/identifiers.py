"""Derive bounded identifiers without dropping the distinguishing suffix."""

from hashlib import sha256


def derived_id(parent: str, suffix: str) -> str:
    if len(parent) + len(suffix) <= 64:
        return parent + suffix
    digest = sha256(parent.encode()).hexdigest()[:12]
    return f"{parent[: 64 - len(suffix) - 13]}-{digest}{suffix}"
