#!/usr/bin/env python3
"""
Configuration encryption utility.

Usage:
    python scripts/encrypt_config.py encrypt config/config.yaml config/config.yaml.enc
    python scripts/encrypt_config.py decrypt config/config.yaml.enc config/config.yaml
"""

import argparse
import getpass
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.utils.encryption import encrypt_data, decrypt_data, is_encrypted


def encrypt_file(input_path: str, output_path: str, password: str) -> None:
    """Encrypt a configuration file."""
    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.exists():
        print(f"Error: Input file '{input_path}' not found")
        sys.exit(1)

    # Read input file
    content = input_file.read_text()

    # Check if already encrypted
    if is_encrypted(content):
        print("Error: File is already encrypted")
        sys.exit(1)

    # Encrypt
    encrypted = encrypt_data(content, password)

    # Write output
    output_file.write_text(encrypted)
    print(f"Successfully encrypted '{input_path}' -> '{output_path}'")


def decrypt_file(input_path: str, output_path: str, password: str) -> None:
    """Decrypt a configuration file."""
    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.exists():
        print(f"Error: Input file '{input_path}' not found")
        sys.exit(1)

    # Read input file
    content = input_file.read_text()

    # Check if encrypted
    if not is_encrypted(content):
        print("Error: File is not encrypted")
        sys.exit(1)

    # Decrypt
    try:
        decrypted = decrypt_data(content, password)
    except Exception as e:
        print(f"Error: Failed to decrypt - {e}")
        sys.exit(1)

    # Write output
    output_file.write_text(decrypted)
    print(f"Successfully decrypted '{input_path}' -> '{output_path}'")


def main():
    parser = argparse.ArgumentParser(
        description="Encrypt or decrypt configuration files"
    )
    parser.add_argument(
        "action",
        choices=["encrypt", "decrypt"],
        help="Action to perform"
    )
    parser.add_argument(
        "input",
        help="Input file path"
    )
    parser.add_argument(
        "output",
        help="Output file path"
    )
    parser.add_argument(
        "--password",
        "-p",
        help="Password (will prompt if not provided)"
    )

    args = parser.parse_args()

    # Get password
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("Enter password: ")
        if args.action == "encrypt":
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                print("Error: Passwords do not match")
                sys.exit(1)

    # Perform action
    if args.action == "encrypt":
        encrypt_file(args.input, args.output, password)
    else:
        decrypt_file(args.input, args.output, password)


if __name__ == "__main__":
    main()
