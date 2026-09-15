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
"""Tests for device password rotation activities."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData
from nv_config_manager.temporal.ngc.activities.device_password_rotation import (
    FormatPasswordRotationResultsInput,
    GetPasswordMappingsInput,
    ValidatePasswordDiffInput,
    ValidatePlatformSupportInput,
    _validate_cumulus_diff,
    _validate_junos_diff,
    format_password_rotation_results,
    get_password_mappings,
    validate_password_diff,
    validate_platform_support,
)


def _password_mapping_client(usernames: set[str]) -> MagicMock:
    """Build a selected-provider client that exposes password mapping users."""
    client = MagicMock()
    client.get_device_password_mapping_users = AsyncMock(return_value=usernames)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestValidatePasswordDiff:
    """Test the password diff validation logic."""

    def test_cumulus_platform_integration(self):
        """Test the main validate_password_diff function with cumulus platform."""
        diff = """nv unset system aaa user cumulus hashed-password *
nv set system aaa user cumulus hashed-password $6$newpassword"""

        input_data = ValidatePasswordDiffInput(diff=diff, username="cumulus", platform="cumulus")

        result = asyncio.run(validate_password_diff(input_data))
        assert result.is_valid is True
        assert len(result.invalid_lines) == 0
        assert len(result.valid_lines) == 2

    def test_unsupported_platform_fails(self):
        """Test that unsupported platform fails validation."""
        diff = """- username admin privilege 15 secret sha512 $6$oldpass
+ username admin privilege 15 secret sha512 $6$newpass"""

        input_data = ValidatePasswordDiffInput(diff=diff, username="admin", platform="arista")

        result = asyncio.run(validate_password_diff(input_data))
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert result.error_message == "No diff parser available for platform: arista"

    def test_mixed_changes_fails(self):
        """Test that diff with password + other changes fails."""
        diff = """nv unset system aaa user cumulus hashed-password *
nv set system aaa user cumulus hashed-password $6$newpassword
nv set interface swp1 ip address 10.1.1.1/24"""

        result = _validate_cumulus_diff(diff, "cumulus")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 1
        assert "nv set interface swp1" in result.invalid_lines[0]
        assert len(result.valid_lines) == 2

    def test_wrong_user_password_fails(self):
        """Test that password changes for different user fails."""
        diff = """nv unset system aaa user admin hashed-password *
nv set system aaa user admin hashed-password $6$newpassword"""

        result = _validate_cumulus_diff(diff, "cumulus")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert all("admin" in line for line in result.invalid_lines)

    def test_no_password_changes_fails(self):
        """Test that diff with no password changes fails."""
        diff = """nv set system hostname new-hostname
nv set system timezone America/New_York"""

        result = _validate_cumulus_diff(diff, "cumulus")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert len(result.valid_lines) == 0
        assert "hostname" in result.invalid_lines[0]
        assert "timezone" in result.invalid_lines[1]

    def test_empty_diff_fails(self):
        """Test that empty diff fails validation."""
        input_data = ValidatePasswordDiffInput(diff="", username="cumulus", platform="cumulus")

        result = asyncio.run(validate_password_diff(input_data))
        assert result.is_valid is False
        assert result.error_message == "Empty diff provided"

    def test_nvos_platform_valid(self):
        """Test NVOS platform password change is valid."""
        diff = """nv unset system aaa user admin password *
nv set system aaa user admin password $6$newpassword"""

        input_data = ValidatePasswordDiffInput(diff=diff, username="admin", platform="nvos")

        result = asyncio.run(validate_password_diff(input_data))
        assert result.is_valid is True
        assert len(result.invalid_lines) == 0

    def test_junos_platform_integration(self):
        """Test the main validate_password_diff function with junos platform."""
        diff = (
            "[edit system login user admin authentication]\n"
            '-   encrypted-password "$6$oldHash"; ## SECRET-DATA\n'
            '+   encrypted-password "$6$newHash"; ## SECRET-DATA'
        )

        input_data = ValidatePasswordDiffInput(diff=diff, username="admin", platform="junos")

        result = asyncio.run(validate_password_diff(input_data))
        assert result.is_valid is True
        assert len(result.invalid_lines) == 0
        assert len(result.valid_lines) == 2

    def test_junos_mixed_changes_fails(self):
        """Test that a Junos diff with password + other changes fails."""
        diff = (
            "[edit system login user admin authentication]\n"
            '-   encrypted-password "$6$oldHash"; ## SECRET-DATA\n'
            '+   encrypted-password "$6$newHash"; ## SECRET-DATA\n'
            "[edit system]\n"
            "-   host-name OLD;\n"
            "+   host-name RTR1;"
        )

        result = _validate_junos_diff(diff, "admin")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert any("host-name" in line for line in result.invalid_lines)
        assert len(result.valid_lines) == 2

    def test_junos_wrong_user_password_fails(self):
        """Test that a Junos password change for a different login user fails."""
        diff = (
            "[edit system login user operator authentication]\n"
            '-   encrypted-password "$6$oldHash"; ## SECRET-DATA\n'
            '+   encrypted-password "$6$newHash"; ## SECRET-DATA'
        )

        result = _validate_junos_diff(diff, "admin")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert len(result.valid_lines) == 0

    def test_junos_root_platform_integration(self):
        """Test the main validate_password_diff function rotating the root user."""
        diff = (
            "[edit system root-authentication]\n"
            '-   encrypted-password "$6$oldHash"; ## SECRET-DATA\n'
            '+   encrypted-password "$6$newHash"; ## SECRET-DATA'
        )

        input_data = ValidatePasswordDiffInput(diff=diff, username="root", platform="junos")

        result = asyncio.run(validate_password_diff(input_data))
        assert result.is_valid is True
        assert len(result.invalid_lines) == 0
        assert len(result.valid_lines) == 2

    def test_junos_root_authentication_rejected_for_other_user(self):
        """Test that a root-authentication change fails validation for a non-root target."""
        diff = (
            "[edit system root-authentication]\n"
            '-   encrypted-password "$6$oldHash"; ## SECRET-DATA\n'
            '+   encrypted-password "$6$newHash"; ## SECRET-DATA'
        )

        result = _validate_junos_diff(diff, "admin")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert len(result.valid_lines) == 0

    def test_junos_no_password_changes_fails(self):
        """Test that a Junos diff with no password changes fails."""
        diff = "[edit system]\n-   host-name OLD;\n+   host-name RTR1;"

        result = _validate_junos_diff(diff, "admin")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 2
        assert len(result.valid_lines) == 0

    def test_junos_header_only_diff_fails(self):
        """Test that a diff with only the user authentication edit stanza header fails.

        A header with no +/- body lines produces empty valid_lines and empty
        invalid_lines; this must not be treated as a validated password change.
        """
        diff = "[edit system login user admin authentication]"

        result = _validate_junos_diff(diff, "admin")
        assert result.is_valid is False
        assert len(result.invalid_lines) == 0
        assert len(result.valid_lines) == 0


class TestGetPasswordMappings:
    """Test password mapping retrieval."""

    def test_get_password_mappings_by_username(self):
        """Test retrieving password mappings keyed by username on device."""
        device = NetworkDeviceData(
            id="device-1",
            name="test-device",
            rack="a01",
            position=1,
            role="TAN-Leaf",
            site="rno1",
            device_type="SN2010",
            platform="cumulus-linux",
            primary_ip4="10.1.1.1",
            primary_ip6=None,
            render_enabled=True,
            deploy_enabled=True,
            backup_enabled=True,
            ztp_enabled=True,
        )

        input_data = GetPasswordMappingsInput(device=device, username="cumulus")

        with patch(
            "nv_config_manager.temporal.ngc.activities.device_password_rotation.create_dcim_client",
            return_value=_password_mapping_client({"cumulus"}),
        ):
            result = asyncio.run(get_password_mappings(input_data))
        assert result.username == "cumulus"

    def test_get_password_mappings_multiple_users(self):
        """Test retrieving password mappings for one of multiple users."""
        device = NetworkDeviceData(
            id="device-1",
            name="test-device",
            rack="a01",
            position=1,
            role="TAN-Leaf",
            site="rno1",
            device_type="SN2010",
            platform="cumulus-linux",
            primary_ip4="10.1.1.1",
            primary_ip6=None,
            render_enabled=True,
            deploy_enabled=True,
            backup_enabled=True,
            ztp_enabled=True,
        )

        input_data = GetPasswordMappingsInput(device=device, username="admin")

        with patch(
            "nv_config_manager.temporal.ngc.activities.device_password_rotation.create_dcim_client",
            return_value=_password_mapping_client({"cumulus", "admin"}),
        ):
            result = asyncio.run(get_password_mappings(input_data))
        assert result.username == "admin"

    def test_get_password_mappings_missing_config(self):
        """Test error when password mappings are missing."""
        device = NetworkDeviceData(
            id="device-1",
            name="test-device",
            rack="a01",
            position=1,
            role="TAN-Leaf",
            site="rno1",
            device_type="SN2010",
            platform="cumulus-linux",
            primary_ip4="10.1.1.1",
            primary_ip6=None,
            render_enabled=True,
            deploy_enabled=True,
            backup_enabled=True,
            ztp_enabled=True,
        )

        input_data = GetPasswordMappingsInput(device=device, username="cumulus")

        with (
            patch(
                "nv_config_manager.temporal.ngc.activities.device_password_rotation.create_dcim_client",
                return_value=_password_mapping_client(set()),
            ),
            pytest.raises(ApplicationError) as exc_info,
        ):
            asyncio.run(get_password_mappings(input_data))
        assert "No password mappings found" in str(exc_info.value)


class TestValidatePlatformSupport:
    """Test platform support validation."""

    def test_cumulus_platform_supported(self):
        """Test that Cumulus Linux platform is supported."""
        input_data = ValidatePlatformSupportInput(platform="cumulus-linux")

        result = asyncio.run(validate_platform_support(input_data))
        assert result.normalized_platform == "cumulus"

    def test_nvos_platform_supported(self):
        """Test that NVOS platform is supported."""
        input_data = ValidatePlatformSupportInput(platform="nv-os")

        result = asyncio.run(validate_platform_support(input_data))
        assert result.normalized_platform == "nvos"

    def test_junos_platform_supported(self):
        """Test that Juniper Junos platform is supported."""
        input_data = ValidatePlatformSupportInput(platform="juniper-junos")

        result = asyncio.run(validate_platform_support(input_data))
        assert result.normalized_platform == "junos"

    def test_unsupported_platform_fails(self):
        """Test that unsupported platform raises error."""
        input_data = ValidatePlatformSupportInput(platform="arista-eos")

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(validate_platform_support(input_data))
        assert "not supported" in str(exc_info.value)


class TestFormatPasswordRotationResults:
    """Test password rotation results formatting."""

    def test_format_all_successful(self):
        """Test formatting when all devices succeed."""
        input_data = FormatPasswordRotationResultsInput(
            successful_devices={
                "device1": {
                    "success": True,
                    "child_workflow_id": "wf-1",
                },
                "device2": {
                    "success": True,
                    "child_workflow_id": "wf-2",
                },
            },
            failed_devices={},
            total_devices=2,
            ui_base_url="temporal.example.com",
        )

        result = format_password_rotation_results(input_data)

        assert "Total devices**: 2" in result
        assert "Updated**: 2" in result
        assert "Not Updated**: 0" in result
        assert "Successfully updated devices:" in result
        assert "device1" in result
        assert "device2" in result
        assert "Devices not updated:" not in result

    def test_format_all_failed(self):
        """Test formatting when all devices fail."""
        input_data = FormatPasswordRotationResultsInput(
            successful_devices={},
            failed_devices={
                "device1": {
                    "success": False,
                    "error": "Error 1",
                    "child_workflow_id": "wf-1",
                },
                "device2": {
                    "success": False,
                    "error": "Error 2",
                    "child_workflow_id": "wf-2",
                },
            },
            total_devices=2,
            ui_base_url="temporal.example.com",
        )

        result = format_password_rotation_results(input_data)

        assert "Total devices**: 2" in result
        assert "Updated**: 0" in result
        assert "Not Updated**: 2" in result
        assert "Devices not updated:" in result
        assert "device1" in result
        assert "device2" in result
        assert "Successfully updated devices:" not in result

    def test_format_mixed_results(self):
        """Test formatting with mixed success and failure."""
        input_data = FormatPasswordRotationResultsInput(
            successful_devices={
                "device1": {
                    "success": True,
                    "child_workflow_id": "wf-1",
                },
            },
            failed_devices={
                "device2": {
                    "success": False,
                    "error": "Diff validation failed",
                    "child_workflow_id": "wf-2",
                },
            },
            total_devices=2,
            ui_base_url="temporal.example.com",
        )

        result = format_password_rotation_results(input_data)

        assert "Total devices**: 2" in result
        assert "Updated**: 1" in result
        assert "Not Updated**: 1" in result
        assert "Successfully updated devices:" in result
        assert "Devices not updated:" in result
        assert "device1" in result
        assert "device2" in result
        assert "Child Workflow Links:" in result

    def test_format_includes_workflow_links(self):
        """Test that formatting includes workflow links."""
        input_data = FormatPasswordRotationResultsInput(
            successful_devices={
                "device1": {
                    "success": True,
                    "child_workflow_id": "wf-123",
                },
            },
            failed_devices={},
            total_devices=1,
            ui_base_url="temporal.example.com",
        )

        result = format_password_rotation_results(input_data)

        assert "https://temporal.example.com/workflows/wf-123" in result
        assert "Child Workflow Links:" in result
        assert "Success" in result
