import base64
import hashlib
from typing import Tuple

from cryptography.fernet import Fernet
from django.conf import settings
from solders.keypair import Keypair


def _fernet() -> Fernet:
    key = settings.SOLANA_WALLET_ENCRYPTION_KEY
    if not key:
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def generate_solana_wallet() -> Tuple[str, str]:
    keypair = Keypair()
    public_key = str(keypair.pubkey())
    secret_bytes = bytes(keypair)
    encrypted_private_key = _fernet().encrypt(secret_bytes).decode()
    return public_key, encrypted_private_key


def load_keypair(encrypted_private_key: str) -> Keypair:
    secret_bytes = _fernet().decrypt(encrypted_private_key.encode())
    return Keypair.from_bytes(secret_bytes)
