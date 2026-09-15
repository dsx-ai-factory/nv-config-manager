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
"""Tests for the command catalog and validate_commands in diagnostics activities."""

import pytest
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.device import Platform
    from nv_config_manager.temporal.ngc.activities.diagnostics import (
        PLATFORM_COMMANDS,
        get_available_commands,
        validate_commands,
    )


# =============================================================================
# get_available_commands
# =============================================================================


def test_get_available_commands_cumulus():
    """Returns a non-empty dict for CUMULUS_LINUX."""
    result = get_available_commands(Platform.CUMULUS_LINUX)
    assert isinstance(result, dict)
    assert len(result) > 0


def test_get_available_commands_cumulus_keys_are_strings():
    """All keys and values in the Cumulus catalog are strings."""
    result = get_available_commands(Platform.CUMULUS_LINUX)
    for name, description in result.items():
        assert isinstance(name, str)
        assert isinstance(description, str)


def test_get_available_commands_arista():
    """Returns a non-empty dict with 21 entries for ARISTA_EOS."""
    result = get_available_commands(Platform.ARISTA_EOS)
    assert isinstance(result, dict)
    assert len(result) == 21


def test_get_available_commands_juniper():
    """JUNIPER_JUNOS exposes exactly the RPC-backed diagnostics, each with a description."""
    result = get_available_commands(Platform.JUNIPER_JUNOS)
    assert set(result) == {
        "show_version",
        "show_interfaces",
        "show_lldp_neighbors",
        "show_route_table",
        "show_arp_table",
    }
    for name, description in result.items():
        assert isinstance(name, str)
        assert isinstance(description, str)
        assert description


def test_validate_commands_juniper():
    """Junos command names normalise and validate against the junos catalog."""
    result = validate_commands(Platform.JUNIPER_JUNOS, ["show version", "show_bgp_summary"])
    assert result == ["show_version"]  # bgp summary is not a junos command


def test_get_available_commands_unknown_platform():
    """Returns {} for a platform not in the catalog — no KeyError raised."""
    for platform in Platform:
        if platform not in PLATFORM_COMMANDS:
            result = get_available_commands(platform)
            assert result == {}
            return
    pytest.skip("All platforms are in the catalog — nothing to test here")


def test_get_available_commands_returns_dict():
    """get_available_commands returns a dict built from master descriptions."""
    result = get_available_commands(Platform.CUMULUS_LINUX)
    assert isinstance(result, dict)


# =============================================================================
# validate_commands
# =============================================================================


def test_validate_commands_exact_match():
    """Known command names pass through unchanged."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["show_version"])
    assert result == ["show_version"]


def test_validate_commands_multiple_exact_match():
    """Multiple known command names all pass through."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["show_version", "show_bgp_summary"])
    assert "show_version" in result
    assert "show_bgp_summary" in result


def test_validate_commands_normalizes_spaces():
    """'show version' (spaces) is normalized to 'show_version' (underscores)."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["show version"])
    assert result == ["show_version"]


def test_validate_commands_normalizes_hyphens():
    """'show-bgp-summary' (hyphens) is normalized to 'show_bgp_summary'."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["show-bgp-summary"])
    assert result == ["show_bgp_summary"]


def test_validate_commands_case_insensitive():
    """'Show_Version' is normalized to 'show_version'."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["Show_Version"])
    assert result == ["show_version"]


def test_validate_commands_strips_whitespace():
    """Leading and trailing whitespace is stripped before matching."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["  show_version  "])
    assert result == ["show_version"]


def test_validate_commands_drops_unknown():
    """Unknown command names are silently dropped; known ones are kept."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["show_version", "nonexistent_command"])
    assert result == ["show_version"]


def test_validate_commands_all_unknown_returns_empty():
    """All unknown commands → empty list."""
    result = validate_commands(Platform.CUMULUS_LINUX, ["fake_cmd_1", "fake_cmd_2"])
    assert result == []


def test_validate_commands_empty_input():
    """Empty input list → empty output list."""
    result = validate_commands(Platform.CUMULUS_LINUX, [])
    assert result == []


def test_validate_commands_empty_platform():
    """Platform with no catalog entries → always returns empty list."""
    result = validate_commands(Platform.MLNX_OS, ["show_version"])
    assert result == []


def test_validate_commands_preserves_input_order():
    """Output order matches the input order, not the catalog order."""
    # Catalog order: show_version first, show_bgp_summary second
    # Input order: bgp first, then version — output should follow input
    result = validate_commands(Platform.CUMULUS_LINUX, ["show_bgp_summary", "show_version"])
    assert result == ["show_bgp_summary", "show_version"]


def test_validate_commands_does_not_deduplicate():
    """validate_commands does not deduplicate — each input occurrence produces one output entry.
    Callers are responsible for deduplicating inputs if needed."""
    result = validate_commands(
        Platform.CUMULUS_LINUX, ["show_version", "show version", "Show_Version"]
    )
    assert result.count("show_version") == 3
