"""API-key helpers. Keys are random 256-bit values, so only a SHA-256 hash is stored."""

from __future__ import annotations

import hashlib
import re
import secrets

from .db import Repository, Tenant

API_KEY_PREFIX = "rw_"
MAX_KEY_LENGTH = 256
_TENANT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def generate_api_key() -> str:
    """A new random API key such as ``rw_Qx...``."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    """SHA-256 hex digest of the key (what is stored in the database)."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def validate_tenant_name(name: str) -> str:
    """Return the stripped name or raise ValueError."""
    cleaned = name.strip()
    if not _TENANT_NAME_RE.match(cleaned):
        raise ValueError(
            "tenant name must be 1-64 characters: letters, digits, spaces, '_', '.' or '-', "
            "starting with a letter or digit"
        )
    return cleaned


def create_tenant(repo: Repository, name: str) -> tuple[Tenant, str]:
    """Create a tenant and return (tenant, plaintext_api_key).

    The plaintext key is only ever available here. Raises ValueError for a bad
    name and DuplicateTenantError if the name is taken.
    """
    cleaned = validate_tenant_name(name)
    api_key = generate_api_key()
    tenant = repo.create_tenant(cleaned, hash_api_key(api_key))
    return tenant, api_key


def extract_api_key(x_api_key: str | None, authorization: str | None) -> str | None:
    """Pick the key from ``X-API-Key`` or ``Authorization: Bearer <key>``."""
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    return None


def authenticate(repo: Repository, api_key: str | None) -> Tenant | None:
    """Return the tenant that owns ``api_key``, or None if it is missing or unknown."""
    if not api_key or len(api_key) > MAX_KEY_LENGTH:
        return None
    return repo.get_tenant_by_key_hash(hash_api_key(api_key))
