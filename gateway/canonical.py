import hashlib
import json
from typing import Any

#Pb solved : Two dicts that are logically equal can serialize differently, especially bc of order of keys (because when transformed into json/string they will be diff)
#-> sort_keys=True

#hash functions operate on bytes -> need to encode string into bytes

def canonical_json(obj: Any) -> bytes:
    """Stable byte encoding for hashing and signing: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

#hexdigest: returns 64-character (256bits, each chr is 4 bit) hexadecimal string
