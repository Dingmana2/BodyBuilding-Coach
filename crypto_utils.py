import base64
import os
from typing import Optional

from cryptography.fernet import Fernet

_fernet: Optional[Fernet] = None
_init_error: Optional[str] = None

_raw = os.getenv("ENCRYPTION_KEY", "")
if not _raw:
    _init_error = "ENCRYPTION_KEY is not configured on this host."
else:
    try:
        _fernet = Fernet(_raw.encode())
    except Exception as _e:
        _init_error = f"ENCRYPTION_KEY is invalid: {_e}"


def encryption_available() -> bool:
    """Return True if a valid ENCRYPTION_KEY is configured."""
    return _fernet is not None


def _get_fernet() -> Fernet:
    """Return the Fernet instance or raise if not configured."""
    if _fernet is None:
        raise RuntimeError(_init_error or "Encryption not available.")
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string and return a base64-encoded token."""
    return base64.b64encode(_get_fernet().encrypt(plaintext.encode())).decode()


def decrypt(token: str) -> str:
    """Decrypt a base64-encoded Fernet token and return the plaintext."""
    return _get_fernet().decrypt(base64.b64decode(token)).decode()
