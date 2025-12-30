"""
Encryption utilities for configuration file protection.

Uses Fernet symmetric encryption with PBKDF2 key derivation.
"""

import base64
import os
import secrets
from typing import Union

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Magic bytes to identify encrypted files
ENCRYPTED_MAGIC = b"FUNDINGARB_ENC_V1:"


def derive_key(password: str, salt: bytes) -> bytes:
    """
    Derive a Fernet-compatible key from a password using PBKDF2.

    Args:
        password: Master password
        salt: Random salt (should be stored with encrypted data)

    Returns:
        Base64-encoded 32-byte key suitable for Fernet
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,  # OWASP recommended minimum
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_data(data: bytes, password: str) -> bytes:
    """
    Encrypt data using Fernet with password-derived key.

    Args:
        data: Plaintext data to encrypt
        password: Master password

    Returns:
        Encrypted data with magic header and salt prefix
    """
    # Generate random salt
    salt = secrets.token_bytes(16)

    # Derive key from password
    key = derive_key(password, salt)

    # Encrypt data
    fernet = Fernet(key)
    encrypted = fernet.encrypt(data)

    # Format: MAGIC + salt (16 bytes) + encrypted data
    return ENCRYPTED_MAGIC + salt + encrypted


def decrypt_data(encrypted_data: bytes, password: str) -> bytes:
    """
    Decrypt data that was encrypted with encrypt_data.

    Args:
        encrypted_data: Encrypted data with magic header and salt
        password: Master password

    Returns:
        Decrypted plaintext data

    Raises:
        ValueError: If data format is invalid
        InvalidToken: If password is incorrect or data is corrupted
    """
    if not is_encrypted(encrypted_data):
        raise ValueError("Data does not appear to be encrypted (missing magic header)")

    # Extract salt and encrypted content
    header_len = len(ENCRYPTED_MAGIC)
    salt = encrypted_data[header_len:header_len + 16]
    encrypted = encrypted_data[header_len + 16:]

    # Derive key from password
    key = derive_key(password, salt)

    # Decrypt
    try:
        fernet = Fernet(key)
        return fernet.decrypt(encrypted)
    except InvalidToken:
        raise ValueError("Decryption failed: incorrect password or corrupted data")


def is_encrypted(data: Union[bytes, str]) -> bool:
    """
    Check if data appears to be encrypted with our format.

    Args:
        data: Data to check

    Returns:
        True if data has encryption magic header
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return data.startswith(ENCRYPTED_MAGIC)


def generate_password(length: int = 32) -> str:
    """
    Generate a secure random password.

    Args:
        length: Password length (default 32 characters)

    Returns:
        Random password string
    """
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))
