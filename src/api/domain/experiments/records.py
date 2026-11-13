"""Restricted experiment record names and finite outbox retention."""
import json
import re

from api.domain.tenant_objects import ObjectInputError

AUDIENCES = ("s3rch", "hypermesh", "portal")
CACHE = re.compile(r"(s3rch|hypermesh|portal)-cache")
OUTBOX = re.compile(r"(s3rch|hypermesh|portal)-outbox-[a-f0-9]{64}")


def physical_record(namespace: str, name: str) -> tuple[str, str]:
    if namespace != "experiments":
        return namespace, name
    if CACHE.fullmatch(name):
        return "cache", "experiments-v1-" + name[:-6]
    if not OUTBOX.fullmatch(name):
        raise ObjectInputError("Invalid experiment record")
    return namespace, name


def outbox_document(data: bytes) -> dict:
    try:
        if len(data) > 65536:
            raise ValueError()
        value = json.loads(data)
        if (not isinstance(value, dict) or set(value) != {"version", "expires", "revoked", "rows"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["expires"]) is not int or not 0 <= value["expires"] <= 9007199254740991
                or type(value["revoked"]) is not bool or not isinstance(value["rows"], list)
                or len(value["rows"]) > 64 or value["revoked"] and value["rows"]):
            raise ValueError()
        # Event payloads remain owned by their producers and event contracts.
        # Storage only validates the retention boundary, not conversion meaning.
        for row in value["rows"]:
            if (not isinstance(row, dict) or set(row) != {"event", "expires", "delivered"}
                    or type(row["expires"]) is not int or not 0 <= row["expires"] <= value["expires"]
                    or not isinstance(row["event"], dict) or not isinstance(row["delivered"], list)):
                raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ObjectInputError("Invalid experiment outbox") from None


def validate_write(name: str, data: bytes, previous: bytes | None, now_ms: int):
    if not OUTBOX.fullmatch(name):
        raise ObjectInputError("Experiment cache is read only")
    value = outbox_document(data)
    if value["expires"] > now_ms + 86400000 or not value["revoked"] and value["expires"] <= now_ms:
        raise ObjectInputError("Experiment retention exceeded")
    if previous is not None:
        prior = outbox_document(previous)
        if value["expires"] != prior["expires"] or prior["revoked"] and not value["revoked"]:
            raise ObjectInputError("Experiment consent cannot be extended or restored")
