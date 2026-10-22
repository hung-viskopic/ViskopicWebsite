# 0004 Per-institution envelope encryption

**Status:** accepted · 2026-10-15

Each institution has a data key, stored only wrapped by a key-encryption key behind a `KeyProvider` interface.
Student numbers, names, original files and extracted text are AES-256-GCM encrypted with it, with the record id
as associated data so ciphertexts can't be swapped between records. Locally the key-encryption key is a master key
in `.env`. In production it will be a key in the university's own KMS, so the university can revoke access to all
of its data at any time, including Viskopic's. Unwrapped keys are cached for at most 5 minutes so revocation takes
effect quickly. Trade-off: encrypted columns can't be searched in SQL; exact-match lookups use a keyed HMAC.
