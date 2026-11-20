# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Helpers for detecting changes to mounted INI files."""

from __future__ import annotations

import hashlib
import os
import threading

type FileFingerprint = tuple[int, int, int, int, int]

DEFAULT_CONFIG_PATH = "/etc/vault/nv-config-manager.ini"

# path, content digest, and text of the first INI a process parsed. Objects
# built at startup hold that version, so the watcher compares against it
# instead of a later read or a later parse: a change that lands after startup
# configuration is loaded is still a change.
_snapshot_lock = threading.Lock()
_loaded_snapshot: tuple[str, str, str] | None = None


def config_path() -> str:
    """Return the path of the unified INI file this process reads.

    Returns:
        NV_CONFIG_MANAGER_INI when set, otherwise the packaged default
    """
    return os.getenv("NV_CONFIG_MANAGER_INI", DEFAULT_CONFIG_PATH)


def file_fingerprint(path: str | None) -> FileFingerprint | None:
    """Return metadata that changes for direct writes and atomic symlink swaps.

    Kubernetes updates Secret volumes by atomically changing the symlink target.
    Following the symlink and including the target inode detects that update even
    when the replacement has the same size and timestamps as the previous file.
    """
    if not path:
        return None

    try:
        stat_result = os.stat(path)
    except OSError:
        return None

    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def remember_loaded_config(path: str, digest: str, text: str) -> None:
    """Record the INI contents of the first successful load of this path."""
    global _loaded_snapshot
    with _snapshot_lock:
        if _loaded_snapshot is None or _loaded_snapshot[0] != path:
            _loaded_snapshot = (path, digest, text)


def loaded_config_snapshot(path: str) -> tuple[str, str] | None:
    """Return the digest and text first parsed for this path."""
    with _snapshot_lock:
        if _loaded_snapshot is None or _loaded_snapshot[0] != path:
            return None
        return _loaded_snapshot[1], _loaded_snapshot[2]


def clear_loaded_config_snapshot() -> None:
    """Drop the parsed snapshot when the configuration cache is cleared."""
    global _loaded_snapshot
    with _snapshot_lock:
        _loaded_snapshot = None


def read_config_snapshot(path: str | None) -> tuple[str, bytes] | None:
    """Read an INI once and return its content digest and raw bytes.

    Hashing and parsing the same read keeps the two from describing different
    versions of a file that Kubernetes can swap at any moment. Kubernetes and
    Vault Agent also rewrite these files on their own schedules with identical
    contents, which the digest reports as no change.

    Args:
        path: File to read

    Returns:
        Hex digest and contents, or None when the file cannot be read
    """
    if not path:
        return None

    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None

    return hashlib.sha256(raw).hexdigest(), raw
