# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Share the last verified Kea configuration hash between pod containers."""

from __future__ import annotations

import os

EXPECTED_CONFIG_HASH_FILE_ENV = "CONFIG_SYNC_EXPECTED_HASH_FILE"
EXPECTED_CONFIG_HASH_FILE = "/tmp/nv-config-manager-dhcp-state/expected-config-hash-v4"  # nosec B108
_MAX_CONFIG_HASH_LENGTH = 128


def expected_config_hash_file() -> str:
    """Return the configured path for the last verified Kea config hash."""
    return os.environ.get(EXPECTED_CONFIG_HASH_FILE_ENV, EXPECTED_CONFIG_HASH_FILE)


def read_expected_config_hash() -> str | None:
    """Read the last verified Kea config hash, or return ``None`` when unavailable."""
    try:
        with open(expected_config_hash_file(), encoding="ascii") as hash_file:
            config_hash = hash_file.read(_MAX_CONFIG_HASH_LENGTH + 1).strip()
    except (OSError, UnicodeError):
        return None
    if not config_hash or len(config_hash) > _MAX_CONFIG_HASH_LENGTH:
        return None
    return config_hash


def write_expected_config_hash(config_hash: str) -> None:
    """Atomically publish the last verified Kea config hash."""
    if not config_hash or len(config_hash) > _MAX_CONFIG_HASH_LENGTH:
        raise ValueError("Kea configuration hash must contain 1-128 characters")

    path = expected_config_hash_file()
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temporary_path = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temporary_path, "w", encoding="ascii") as hash_file:
            os.chmod(temporary_path, 0o600)
            hash_file.write(f"{config_hash}\n")
            hash_file.flush()
            os.fsync(hash_file.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
