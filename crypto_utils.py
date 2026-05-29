import base64
import os

from cryptography.fernet import Fernet

_raw = os.getenv("ENCRYPTION_KEY", "")
if not _raw:
    raise RuntimeError(
        "ENCRYPTION_KEY environment variable is not set. "
        "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
        "and add it to your .env file."
    )
try:
    _fernet = Fernet(_raw.encode())
except Exception as e:
    raise RuntimeError(f"ENCRYPTION_KEY is invalid: {e}") from e


def encrypt(plaintext: str) -> str:
    return base64.b64encode(_fernet.encrypt(plaintext.encode())).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(base64.b64decode(token)).decode()
