from __future__ import annotations

import base64
import hashlib
import uuid

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False

# Domain 2: Cryptographic & Deterministic Identification (Crypto Domain)
# ==============================================================================

def b64u(b: bytes) -> str:
    """Encode bytes to URL-safe base64 string without trailing '=' padding."""
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def derive_keypair(seed: str | None, seed_host: str) -> tuple[str, str]:
    """Derive X25519 private and public keys deterministically from seed+host, or randomly."""
    if not HAVE_CRYPTOGRAPHY:
        raise RuntimeError("The 'cryptography' library is required for X25519 key derivation.")

    if seed:
        raw_priv_seed = hashlib.sha256(f"{seed}:{seed_host}".encode("utf-8")).digest()
        priv = x25519.X25519PrivateKey.from_private_bytes(raw_priv_seed)
    else:
        priv = x25519.X25519PrivateKey.generate()

    pub = priv.public_key()
    raw_priv = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_pub = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return b64u(raw_priv), b64u(raw_pub)


def derive_user_uuids(count: int, seed: str | None, seed_host: str) -> list[str]:
    """Derive client UUIDs deterministically from seed+host or generate random v4 UUIDs."""
    if seed:
        ns = uuid.UUID(int=0)
        return [str(uuid.uuid5(ns, f"{seed}:{seed_host}:{i}")) for i in range(count)]
    else:
        return [str(uuid.uuid4()) for _ in range(count)]


# ==============================================================================
