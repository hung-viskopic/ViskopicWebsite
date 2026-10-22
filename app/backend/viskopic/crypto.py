"""Envelope encryption with per-institution data keys.

Each institution has its own data encryption key (DEK). The DEK is stored only in
wrapped (encrypted) form in `institutions.wrapped_dek`; unwrapping it needs the
institution's key-encryption key, which lives in a KeyProvider.

- LocalKeyProvider (development): the key-encryption key is a master key from the
  environment.
- Production: a KMS-backed provider where the key-encryption key sits in the
  institution's own cloud account, so the institution can revoke it and make all
  of its stored data unreadable, including to Viskopic.
"""
import base64
import hashlib
import hmac
import os
import threading
import time
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import get_settings

_VERSION = b"v1"


class KeyProvider(Protocol):
    def new_wrapped_key(self, key_ref: str) -> bytes: ...
    def unwrap(self, key_ref: str, wrapped: bytes) -> bytes: ...


class LocalKeyProvider:
    def __init__(self, master_key: bytes):
        if len(master_key) != 32:
            raise ValueError("local master key must be 32 bytes (base64-encoded in VISKOPIC_LOCAL_MASTER_KEY_B64)")
        self._aes = AESGCM(master_key)

    def new_wrapped_key(self, key_ref: str) -> bytes:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, dek, key_ref.encode())

    def unwrap(self, key_ref: str, wrapped: bytes) -> bytes:
        return self._aes.decrypt(wrapped[:12], wrapped[12:], key_ref.encode())


_provider: KeyProvider | None = None


def get_key_provider() -> KeyProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        if s.key_provider != "local":
            raise RuntimeError(f"key provider {s.key_provider!r} is not implemented yet")
        if not s.local_master_key_b64:
            raise RuntimeError("VISKOPIC_LOCAL_MASTER_KEY_B64 is not set")
        _provider = LocalKeyProvider(base64.b64decode(s.local_master_key_b64))
    return _provider


def set_key_provider(provider: KeyProvider | None) -> None:
    global _provider
    _provider = provider
    _cache.clear()


# Unwrapped keys are cached briefly in memory so a revoked KMS key takes effect quickly.
_CACHE_TTL = 300
_cache: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()


def institution_key(institution_id: UUID | str, key_ref: str, wrapped: bytes) -> bytes:
    k = str(institution_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(k)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    dek = get_key_provider().unwrap(key_ref, bytes(wrapped))
    with _lock:
        _cache[k] = (now, dek)
    return dek


def encrypt(dek: bytes, plaintext: bytes, aad: str) -> bytes:
    nonce = os.urandom(12)
    return _VERSION + nonce + AESGCM(dek).encrypt(nonce, plaintext, aad.encode())


def decrypt(dek: bytes, blob: bytes, aad: str) -> bytes:
    blob = bytes(blob)
    if blob[:2] != _VERSION:
        raise ValueError("unknown ciphertext version")
    return AESGCM(dek).decrypt(blob[2:14], blob[14:], aad.encode())


def lookup_hmac(dek: bytes, purpose: str, value: str) -> bytes:
    """Deterministic keyed hash for exact-match lookups of encrypted values."""
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"lookup:{purpose}".encode()).derive(dek)
    return hmac.new(key, value.strip().lower().encode(), hashlib.sha256).digest()
