"""Check that a receipt is covered by an external anchor, from public data only.

A proof is: the public links (seq, time, event, payload hash, previous hash, hash) from the
receipt's log entry up to an anchored entry, plus the timestamp authority's token over that
anchored entry's hash. Anyone can re-hash the links and check the token; no private log
contents and no trust in the gateway are needed.
"""

import base64
from collections.abc import Callable
from typing import Any

from gateway.ledger.anchor import verify_token
from gateway.ledger.chain import verify_links


def verify_receipt_proof(proof: dict[str, Any], receipt_log_hash: str,
                         token_verifier: Callable[[bytes, str], dict] = verify_token
                         ) -> dict[str, Any]:
    if not proof.get("anchored"):
        return {"anchored": False, "valid": None,
                "detail": proof.get("reason", "not witnessed yet")}
    links = proof.get("links") or []
    anchor = proof["anchor"]
    if not links or links[0]["hash"] != receipt_log_hash:
        return {"anchored": True, "valid": False,
                "detail": "proof does not start at the receipt's log entry"}
    if not verify_links(links):
        return {"anchored": True, "valid": False, "detail": "log links do not re-hash"}
    if links[-1]["hash"] != anchor["head_hash"] or links[-1]["seq"] != anchor["seq"]:
        return {"anchored": True, "valid": False,
                "detail": "proof does not end at the anchored entry"}
    tok = token_verifier(base64.b64decode(anchor["token"]), anchor["head_hash"])
    return {
        "anchored": True,
        "valid": bool(tok.get("valid")),
        "detail": tok.get("detail"),
        "gen_time": tok.get("gen_time"),
        "tsa_name": tok.get("tsa_name"),
        "trusted_by": tok.get("trusted_by"),
        "anchor_id": anchor.get("id"),
        "anchor_seq": anchor["seq"],
        "links": len(links),
    }
