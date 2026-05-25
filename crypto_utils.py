import base64
import os

from cryptography.fernet import Fernet

_raw = os.getenv("ENCRYPTION_KEY", "")
try:
    _fernet = Fernet(_raw.encode() if _raw else Fernet.generate_key())
except Exception:
    _fernet = Fernet(Fernet.generate_key())


def encrypt(plaintext: str) -> str:
    return base64.b64encode(_fernet.encrypt(plaintext.encode())).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(base64.b64decode(token)).decode()
