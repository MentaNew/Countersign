"""Generate fresh secrets for the hosted (Render) deployment and write them to
keys/render-secrets.env (gitignored, mode 0600). Nothing is printed, so secrets never land in a
terminal log. Open the file and paste each block into the matching Render service.

    uv run python scripts/render_secrets.py          # refuses to overwrite an existing file
    uv run python scripts/render_secrets.py --force  # new secrets (old receipts stop verifying
                                                     # with the new gateway key)

The hosted deployment gets its own keys, separate from your laptop's, on purpose.
"""

import base64
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OUT = Path(__file__).resolve().parent.parent / "keys" / "render-secrets.env"


def pem_b64(key) -> str:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return base64.b64encode(pem).decode()


def main() -> int:
    if OUT.exists() and "--force" not in sys.argv:
        print(f"{OUT} already exists; use --force to replace it")
        return 1
    agent_key = secrets.token_urlsafe(32)
    text = f"""# Secrets for the hosted deployment. Never commit or share this file.
# Paste each block into the Render service named in its header (Environment -> Add from .env).

# --- spend-guardrail-gw (gateway) ---
SIGNING_KEY_PEM={pem_b64(Ed25519PrivateKey.generate())}
AP2_SIGNING_KEY_PEM={pem_b64(ec.generate_private_key(ec.SECP256R1()))}
AGENT_API_KEY={agent_key}
USER_PASSWORD={secrets.token_urlsafe(18)}

# --- spend-guardrail-desk (dispute desk) ---
SELLER_PASSWORD={secrets.token_urlsafe(18)}

# --- spend-guardrail-shops (demo shops) ---
MERCHANT_KEY_SEED={secrets.token_urlsafe(32)}

# --- Also needed, copy from your .env (same values on gateway and desk) ---
# PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, and on the gateway optionally PAYPAL_VAULT_ID and
# GROQ_API_KEY (only if you set VERIFIER=llm). PAYPAL_WEBHOOK_ID on the desk comes later.
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.touch(mode=0o600)
    OUT.write_text(text)
    OUT.chmod(0o600)
    print(f"wrote {OUT} (not printed here; open it to copy the values)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
