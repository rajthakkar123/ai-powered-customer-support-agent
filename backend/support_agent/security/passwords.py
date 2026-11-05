"""scrypt password hashing using only the standard library."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class PasswordHasher:
    """Stores cost parameters with each hash so they can be raised without breaking logins."""

    n: int = 2**14
    r: int = 8
    p: int = 1

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = _derive(password, salt, self.n, self.r, self.p)
        return "$".join(("scrypt", str(self.n), str(self.r), str(self.p), _b64(salt), _b64(digest)))

    def verify(self, password: str, encoded: str) -> bool:
        try:
            scheme, n, r, p, salt, digest = encoded.split("$")
            if scheme != "scrypt":
                return False
            actual = _derive(password, _unb64(salt), int(n), int(r), int(p))
            return hmac.compare_digest(actual, _unb64(digest))
        except ValueError:
            # Malformed hashes and invalid parameters never authenticate.
            return False


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
