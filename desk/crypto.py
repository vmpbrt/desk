"""AES-256-GCM for every published data file + HMAC-opaque filenames. Key never enters the repo."""
import base64, hashlib, hmac, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRETS_DIR = os.path.expanduser("~/.desk_secrets")
KEY_PATH = os.path.join(SECRETS_DIR, "data_key")


def load_or_create_key(path=KEY_PATH):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
    os.chmod(path, 0o600)
    with open(path, "rb") as f:
        k = f.read()
    assert len(k) == 32
    return k


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def file_name(key, logical):
    return hmac.new(key, logical.encode(), hashlib.sha256).hexdigest()[:32] + ".bin"


def encrypt(key, plaintext: bytes):
    iv = os.urandom(12)
    return iv + AESGCM(key).encrypt(iv, plaintext, None)


def decrypt(key, blob: bytes):
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)
