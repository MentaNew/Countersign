"""External anchoring of the audit log with RFC 3161 timestamp authorities.

The gateway periodically sends the hash of the newest log entry (the "chain head") to a public
timestamp authority (DigiCert by default, FreeTSA as fallback). The authority signs "this hash
existed at this time". Because every log entry commits to the one before it, rewriting any entry
at or before an anchored one changes the head hash and no longer matches the authority's
signature, so even the gateway's operator cannot rewrite history without it showing.

Tokens are standard (RFC 3161 / CMS): besides `verify_token` here, anyone can check one with
`openssl ts -verify -digest <head_hash> -in anchor.tsr -CAfile <tsa_root.pem>`.
"""

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import certifi
import httpx
from asn1crypto import algos, cms, pem, tsp, x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key

DEFAULT_TSAS = ("http://timestamp.digicert.com", "https://freetsa.org/tsr")

# FreeTSA's root is not in public trust stores; pinned from the certificate it served on
# 2026-10-21 (check against https://freetsa.org/files/cacert.pem before relying on it).
PINNED_ROOTS_SHA256: dict[str, str] = {
    "FreeTSA Root CA": "a6379e7cecc05faa3cbf076013d745e327bbbaa38c0b9af22469d4701d18aabc",
}


class AnchorError(Exception):
    pass


@dataclass
class Timestamp:
    tsa_url: str
    token: bytes  # DER TimeStampToken (CMS ContentInfo)
    gen_time: datetime
    tsa_name: str


def build_request(digest: bytes) -> bytes:
    return tsp.TimeStampReq({
        "version": 1,
        "message_imprint": tsp.MessageImprint({
            "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "hashed_message": digest,
        }),
        "nonce": int.from_bytes(os.urandom(8), "big"),
        "cert_req": True,
    }).dump()


async def request_timestamp(digest: bytes, tsa_urls: tuple[str, ...] = DEFAULT_TSAS,
                            client: httpx.AsyncClient | None = None) -> Timestamp:
    """Ask the first authority that answers to timestamp `digest` (a SHA-256)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    errors = []
    try:
        for url in tsa_urls:
            try:
                r = await client.post(url, content=build_request(digest),
                                      headers={"Content-Type": "application/timestamp-query"})
                r.raise_for_status()
                resp = tsp.TimeStampResp.load(r.content)
                if resp["status"]["status"].native not in ("granted", "granted_with_mods"):
                    raise AnchorError(f"refused: {resp['status']['status'].native}")
                token = resp["time_stamp_token"].dump()
                info = parse_token(token)
                if info["imprint"] != digest.hex():
                    raise AnchorError("authority timestamped a different hash")
                return Timestamp(url, token, info["gen_time"], info["tsa_name"])
            except (httpx.HTTPError, ValueError, AnchorError) as e:
                errors.append(f"{url}: {e}")
    finally:
        if own:
            await client.aclose()
    raise AnchorError("; ".join(errors) or "no timestamp authority configured")


def parse_token(token: bytes) -> dict[str, Any]:
    sd = cms.ContentInfo.load(token)["content"]
    tst = sd["encap_content_info"]["content"].parsed
    signer_cert = _signer_cert(sd)
    return {
        "gen_time": tst["gen_time"].native,
        "imprint": tst["message_imprint"]["hashed_message"].native.hex(),
        "serial": str(tst["serial_number"].native),
        "tsa_name": signer_cert.subject.native.get("common_name", "")
        if signer_cert else "",
    }


def _signer_cert(sd) -> x509.Certificate | None:
    sid = sd["signer_infos"][0]["sid"]
    for c in sd["certificates"] or []:
        cert = c.chosen
        if sid.name == "issuer_and_serial_number":
            if (cert.issuer == sid.chosen["issuer"]
                    and cert.serial_number == sid.chosen["serial_number"].native):
                return cert
        elif cert.key_identifier == sid.chosen.native:
            return cert
    return None


def _hash(name: str) -> hashes.HashAlgorithm:
    return {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(),
            "sha512": hashes.SHA512(), "sha1": hashes.SHA1()}[name]


def _verify_sig(public_der: bytes, signature: bytes, data: bytes, algo: str, hash_name: str,
                pss_params=None) -> None:
    key = load_der_public_key(public_der)
    h = _hash(hash_name)
    if isinstance(key, rsa.RSAPublicKey):
        if algo == "rsassa_pss":
            key.verify(signature, data, padding.PSS(mgf=padding.MGF1(h), salt_length=padding.PSS.AUTO), h)
        else:
            key.verify(signature, data, padding.PKCS1v15(), h)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        key.verify(signature, data, ec.ECDSA(h))
    else:
        raise AnchorError("unsupported key type")


@lru_cache
def _public_roots() -> dict[bytes, list[x509.Certificate]]:
    """The public web trust store (Mozilla's, via certifi), indexed by subject."""
    out: dict[bytes, list[x509.Certificate]] = {}
    with open(certifi.where(), "rb") as f:
        for _, _, der in pem.unarmor(f.read(), multiple=True):
            cert = x509.Certificate.load(der)
            out.setdefault(cert.subject.dump(), []).append(cert)
    return out


def _trusted_root_for(cert: x509.Certificate) -> x509.Certificate | None:
    """A trust-store root with the same name and key as `cert` (handles cross-signed roots)."""
    for root in _public_roots().get(cert.subject.dump(), []):
        if root.public_key.dump() == cert.public_key.dump():
            return root
    return None


def verify_token(token: bytes, digest_hex: str) -> dict[str, Any]:
    """Check an anchor token from scratch: the timestamped hash, the authority's CMS signature,
    and its certificate chain up to a root that is publicly trusted (or pinned)."""
    out: dict[str, Any] = {"valid": False}
    try:
        sd = cms.ContentInfo.load(token)["content"]
        eci = sd["encap_content_info"]
        tst = eci["content"].parsed
        out["gen_time"] = tst["gen_time"].native.isoformat()
        if tst["message_imprint"]["hashed_message"].native.hex() != digest_hex:
            out["detail"] = "the authority timestamped a different hash"
            return out
        signer = sd["signer_infos"][0]
        cert = _signer_cert(sd)
        if cert is None:
            out["detail"] = "signing certificate missing"
            return out
        out["tsa_name"] = cert.subject.native.get("common_name", "")
        digest_alg = signer["digest_algorithm"]["algorithm"].native
        md = next(a["values"][0].native for a in signer["signed_attrs"]
                  if a["type"].native == "message_digest")
        if hashlib.new(digest_alg, eci["content"].contents).digest() != md:
            out["detail"] = "timestamp content does not match its signed digest"
            return out
        signed_attrs = signer["signed_attrs"].dump()
        signed_attrs = b"\x31" + signed_attrs[1:]  # signature is over the SET OF encoding
        _verify_sig(cert.public_key.dump(), signer["signature"].native, signed_attrs,
                    signer["signature_algorithm"].signature_algo, digest_alg)
        if "time_stamping" not in (cert.extended_key_usage_value.native
                                   if cert.extended_key_usage_value else []):
            out["detail"] = "signing certificate is not a timestamping certificate"
            return out
        # Walk the chain, checking each signature, until a trusted (or pinned) root.
        certs = [c.chosen for c in sd["certificates"]]
        current, trusted_by = cert, None
        for _ in range(6):
            if _trusted_root_for(current):
                trusted_by = "public trust store"
                break
            if hashlib.sha256(current.dump()).hexdigest() in PINNED_ROOTS_SHA256.values():
                trusted_by = "pinned root"
                break
            if current.subject == current.issuer:
                break  # self-signed but not trusted
            issuer = next((c for c in certs if c.subject == current.issuer), None)
            if issuer is None:  # not in the token: look in the trust store
                issuer = next(iter(_public_roots().get(current.issuer.dump(), [])), None)
            if issuer is None:
                break
            _verify_sig(issuer.public_key.dump(), current["signature_value"].native,
                        current["tbs_certificate"].dump(), current.signature_algo,
                        current.hash_algo)
            current = issuer
        out["root"] = current.subject.native.get("common_name", "")
        out["trusted_by"] = trusted_by
        out["valid"] = trusted_by is not None
        out["detail"] = (f"signed by a trusted timestamp authority ({trusted_by})" if out["valid"]
                         else "signature valid but the authority's root is not trusted here")
    except (InvalidSignature, ValueError, KeyError, StopIteration, TypeError) as e:
        out["detail"] = f"invalid token ({type(e).__name__})"
    return out
