# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for sharing the verified Kea configuration hash."""

from pathlib import Path

import pytest

from nv_config_manager.dhcp import config_hash


def test_write_expected_config_hash_returns_false_for_oserror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A filesystem failure leaves readiness unpublished without escaping."""
    expected_hash_file = tmp_path / "expected-config-hash-v4"
    monkeypatch.setenv(config_hash.EXPECTED_CONFIG_HASH_FILE_ENV, str(expected_hash_file))

    def fail_to_create_directory(*args, **kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(config_hash.os, "makedirs", fail_to_create_directory)

    assert config_hash.write_expected_config_hash("verified-hash") is False


def test_write_expected_config_hash_does_not_swallow_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Programming errors still propagate instead of masquerading as I/O failures."""
    expected_hash_file = tmp_path / "expected-config-hash-v4"
    monkeypatch.setenv(config_hash.EXPECTED_CONFIG_HASH_FILE_ENV, str(expected_hash_file))

    def fail_unexpectedly(*args, **kwargs) -> None:
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(config_hash.os, "makedirs", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="unexpected failure"):
        config_hash.write_expected_config_hash("verified-hash")


def test_write_expected_config_hash_ignores_cleanup_oserror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cleanup failure cannot terminate config-sync after a write failure."""
    expected_hash_file = tmp_path / "expected-config-hash-v4"
    monkeypatch.setenv(config_hash.EXPECTED_CONFIG_HASH_FILE_ENV, str(expected_hash_file))

    def fail_to_replace(*args, **kwargs) -> None:
        raise OSError("replace failed")

    def fail_to_unlink(*args, **kwargs) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(config_hash.os, "replace", fail_to_replace)
    monkeypatch.setattr(config_hash.os, "unlink", fail_to_unlink)

    assert config_hash.write_expected_config_hash("verified-hash") is False


def test_write_expected_config_hash_does_not_swallow_unexpected_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unexpected cleanup errors remain visible to expose programming defects."""
    expected_hash_file = tmp_path / "expected-config-hash-v4"
    monkeypatch.setenv(config_hash.EXPECTED_CONFIG_HASH_FILE_ENV, str(expected_hash_file))

    def fail_to_replace(*args, **kwargs) -> None:
        raise OSError("replace failed")

    def fail_unexpectedly(*args, **kwargs) -> None:
        raise RuntimeError("unexpected cleanup failure")

    monkeypatch.setattr(config_hash.os, "replace", fail_to_replace)
    monkeypatch.setattr(config_hash.os, "unlink", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="unexpected cleanup failure"):
        config_hash.write_expected_config_hash("verified-hash")
