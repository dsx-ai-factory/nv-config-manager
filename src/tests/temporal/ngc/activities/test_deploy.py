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
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import responses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.device import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    ConfigApplyFailureException,
    InvalidConfigException,
)
from nv_config_manager.temporal.common.mixins.device import NetworkDeviceData, Platform
from nv_config_manager.temporal.ngc.activities.deploy import (
    ConfigApplyActivityInput,
    DiffActivityInput,
    LoadPartialConfigurationActivityInput,
    WaitForTenantRenderInput,
    apply_approved_configuration,
    load_partial_configuration,
    perform_candidate_diff,
    wait_for_tenant_render,
)
from tests.temporal.ngc.activities.test_device_data import (
    CUMULUS_DHCP_DIFF,
    CUMULUS_DIFF,
    CUMULUS_IGNORE_FAIL_CONFIG,
    CUMULUS_IGNORE_FAIL_NO_TRANSITION,
    CUMULUS_INVALID_CONFIG,
)


@responses.activate
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._load_candidate", return_value="2"
)
def test_cumulus_diff(mock_load):
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/?rev=applied&diff=2&filled=false",
        json=CUMULUS_DIFF["removed"],
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/?rev=2&diff=applied&filled=false",
        json=CUMULUS_DIFF["added"],
    )

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = DiffActivityInput(device_data=device_data, configuration="test")

    diff = perform_candidate_diff(activity_input)

    expected_diff = """
nv unset interface lo description test123
nv unset service syslog mgmt server 1.1.1.1 port 32365
nv unset service syslog mgmt server 1.1.1.1 protocol udp
nv set interface swp1 description test description
"""

    assert diff.strip() == expected_diff.strip()


@responses.activate
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._load_candidate", return_value="2"
)
def test_cumulus_diff_dhcp(mock_load):
    # Saw issues parsing this specific output, testing directly
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/?rev=applied&diff=2&filled=false",
        json=CUMULUS_DHCP_DIFF["removed"],
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/?rev=2&diff=applied&filled=false",
        json=CUMULUS_DHCP_DIFF["added"],
    )

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = DiffActivityInput(device_data=device_data, configuration="test")

    diff = perform_candidate_diff(activity_input)
    expected_diff = """
nv set service dhcp-relay default interface swp49
nv set service dhcp-relay default interface swp50
nv set service dhcp-relay default interface vlan112
nv set service dhcp-relay default interface vlan12
nv set service dhcp-relay default server 10.91.208.128
"""
    assert diff.strip() == expected_diff.strip()


@responses.activate
@patch("nv_config_manager.temporal.client.device.CumulusConnection._apply_config")
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._get_diff",
    return_value="nv set aaa user cumulus hashed-password '*'",
)
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._load_candidate", return_value="2"
)
def test_apply_approved_configuration_invalid_config(mock_load, mock_diff, mock_apply):
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/revision/2",
        json=CUMULUS_INVALID_CONFIG,
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/revision/applied",
        json={
            "state": "saved",
        },
    )

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="nv set aaa user cumulus hashed-password '*'",
        approved_diff="nv set aaa user cumulus hashed-password '*'",
    )

    with pytest.raises(InvalidConfigException):
        apply_approved_configuration(activity_input)


def test_config_apply_failure_exception_format_nvue_apply_error_no_issue():
    """format_nvue_apply_error returns json.dumps when transition_data has no issue."""
    assert "Configuration apply failed" in (ConfigApplyFailureException.format_nvue_apply_error({}))
    assert "Configuration apply failed" in (
        ConfigApplyFailureException.format_nvue_apply_error({"state": "ignore_fail"})
    )
    assert "Configuration apply failed" in (
        ConfigApplyFailureException.format_nvue_apply_error({"issue": {}})
    )
    assert json.dumps({"other": 1}) in (
        ConfigApplyFailureException.format_nvue_apply_error({"other": 1})
    )


def test_config_apply_failure_exception_format_nvue_apply_error_with_issues():
    """format_nvue_apply_error returns progress and formatted errors when issue present."""
    transition = CUMULUS_IGNORE_FAIL_CONFIG["transition"]
    result = ConfigApplyFailureException.format_nvue_apply_error(transition)
    assert "Failure during apply. Ignore?" in result
    assert "[ERROR]" in result
    assert "systemctl" in result
    assert "Unable to reload-or-restart services (frr)" in result
    # Defaults when key missing
    transition_minimal = {"issue": {"0": {"message": "Only message"}}}
    result_minimal = ConfigApplyFailureException.format_nvue_apply_error(transition_minimal)
    assert "Configuration apply failed" in result_minimal  # default progress
    assert "[UNKNOWN]" in result_minimal  # default severity
    assert "Only message" in result_minimal


@responses.activate
@patch("nv_config_manager.temporal.client.device.CumulusConnection._apply_config")
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._get_diff",
    return_value="nv set aaa user cumulus hashed-password '*'",
)
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._load_candidate", return_value="2"
)
def test_apply_approved_configuration_ignore_fail_with_transition(mock_load, mock_diff, mock_apply):
    """Config apply raises ConfigApplyFailureException with formatted message on ignore_fail + transition."""
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/revision/2",
        json=CUMULUS_IGNORE_FAIL_CONFIG,
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/revision/applied",
        json={"state": "saved"},
    )

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="nv set aaa user cumulus hashed-password '*'",
        approved_diff="nv set aaa user cumulus hashed-password '*'",
    )

    with pytest.raises(ConfigApplyFailureException) as exc_info:
        apply_approved_configuration(activity_input)

    assert exc_info.value.non_retryable is True
    assert "Failure during apply. Ignore?" in str(exc_info.value)
    assert "frr.service failed" in str(exc_info.value)
    assert "MANUAL INTERVENTION REQUIRED" in str(exc_info.value)
    assert "nv config apply" in str(exc_info.value)


@responses.activate
@patch("nv_config_manager.temporal.client.device.CumulusConnection._apply_config")
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._get_diff",
    return_value="nv set aaa user cumulus hashed-password '*'",
)
@patch(
    "nv_config_manager.temporal.client.device.CumulusConnection._load_candidate", return_value="2"
)
def test_apply_approved_configuration_ignore_fail_without_transition(
    mock_load, mock_diff, mock_apply
):
    """Config apply raises ConfigApplyFailureException with default message when ignore_fail has no transition."""
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/revision/2",
        json=CUMULUS_IGNORE_FAIL_NO_TRANSITION,
    )
    responses.add(
        responses.GET,
        "https://192.0.2.1:8765/nvue_v1/revision/applied",
        json={"state": "saved"},
    )

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="nv set aaa user cumulus hashed-password '*'",
        approved_diff="nv set aaa user cumulus hashed-password '*'",
    )

    with pytest.raises(ConfigApplyFailureException) as exc_info:
        apply_approved_configuration(activity_input)

    assert exc_info.value.non_retryable is True
    assert "Config apply failed with ignore_fail state" in str(exc_info.value)
    assert "MANUAL INTERVENTION REQUIRED" in str(exc_info.value)


@pytest.mark.asyncio
async def test_load_partial_configuration_at_pinned_commit():
    """Load the exact tenant config version supplied by the render snapshot."""
    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.get_config_file = AsyncMock(
        return_value={"content": "nv set interface swp1 ip vrf tenant", "version": 7}
    )
    mock_config_client.load_file = AsyncMock()
    mock_config_client.file_url = MagicMock(return_value="https://config-store/tenant.yaml?v=7")

    device_data = NetworkDeviceData(
        id="test-device-id",
        name="test-device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    with patch(
        "nv_config_manager.temporal.ngc.activities.deploy.config_store_client",
        return_value=mock_config_client,
    ):
        result = await load_partial_configuration(
            LoadPartialConfigurationActivityInput(
                device_data=device_data,
                config_file="tenant.yaml",
                commit_id="7",
            )
        )

    assert result == (
        "nv set interface swp1 ip vrf tenant",
        "7",
        "https://config-store/tenant.yaml?v=7",
    )
    mock_config_client.get_config_file.assert_awaited_once_with(
        device_uuid="test-device-id",
        filename="tenant.yaml",
        version=7,
    )
    mock_config_client.load_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_tenant_render_success():
    """Test wait_for_tenant_render when config_id matches."""
    mock_file = MagicMock()
    mock_file.commit = "5"  # Numeric commit ID

    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    device_data = NetworkDeviceData(
        id="test-device-id",
        name="test-device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
        tenant_config_file="tenant.yaml",
    )

    with patch(
        "nv_config_manager.temporal.ngc.activities.deploy.config_store_client",
        return_value=mock_config_client,
    ):
        activity_input = WaitForTenantRenderInput(
            device=device_data,
            config_id="5",
            interval=1,
            max_attempts=5,
        )

        result = await wait_for_tenant_render(activity_input)

    assert result.config_id == "5"
    mock_config_client.load_file.assert_called_once()


@pytest.mark.asyncio
async def test_wait_for_tenant_render_timeout():
    """Test wait_for_tenant_render when config_id doesn't match within time."""
    mock_file = MagicMock()
    mock_file.commit = "3"  # Older commit ID (3 < 5)

    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    device_data = NetworkDeviceData(
        id="test-device-id",
        name="test-device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
        tenant_config_file="tenant.yaml",
    )

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.deploy.config_store_client",
            return_value=mock_config_client,
        ),
        patch(
            "nv_config_manager.temporal.ngc.activities.deploy.asyncio.sleep", new_callable=AsyncMock
        ),
    ):
        activity_input = WaitForTenantRenderInput(
            device=device_data,
            config_id="5",
            interval=1,
            max_attempts=3,
        )

        with pytest.raises(ApplicationError) as exc_info:
            await wait_for_tenant_render(activity_input)

        assert "Tenant render not available after" in str(exc_info.value)

    assert mock_config_client.load_file.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_tenant_render_wrong_commit():
    """Test wait_for_tenant_render when commit is older than expected."""
    mock_file = MagicMock()
    mock_file.commit = "3"  # Older commit ID (3 < 5)

    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    device_data = NetworkDeviceData(
        id="test-device-id",
        name="test-device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
        tenant_config_file="tenant.yaml",
    )

    with (
        patch(
            "nv_config_manager.temporal.ngc.activities.deploy.config_store_client",
            return_value=mock_config_client,
        ),
        patch(
            "nv_config_manager.temporal.ngc.activities.deploy.asyncio.sleep", new_callable=AsyncMock
        ),
    ):
        activity_input = WaitForTenantRenderInput(
            device=device_data,
            config_id="5",
            interval=1,
            max_attempts=3,
        )

        with pytest.raises(ApplicationError) as exc_info:
            await wait_for_tenant_render(activity_input)

        assert "Tenant render not available after" in str(exc_info.value)

    assert mock_config_client.load_file.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_tenant_render_no_config_id():
    """Test wait_for_tenant_render when config_id is None."""
    mock_file = MagicMock()
    mock_file.commit = "any-commit-id"

    mock_config_client = AsyncMock()
    mock_config_client.__aenter__ = AsyncMock(return_value=mock_config_client)
    mock_config_client.__aexit__ = AsyncMock(return_value=None)
    mock_config_client.load_file = AsyncMock(return_value=mock_file)

    device_data = NetworkDeviceData(
        id="test-device-id",
        name="test-device",
        platform="cumulus-linux",
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
        tenant_config_file="tenant.yaml",
    )

    with patch(
        "nv_config_manager.temporal.ngc.activities.deploy.config_store_client",
        return_value=mock_config_client,
    ):
        activity_input = WaitForTenantRenderInput(
            device=device_data,
            config_id=None,
            interval=1,
            max_attempts=5,
        )

        result = await wait_for_tenant_render(activity_input)

    assert result.config_id is None
    mock_config_client.load_file.assert_called_once()


# --- commit_confirm activity tests ---
#
# Cumulus: Mock only HTTP via responses; assert on actual PATCH request bodies.
# Arista: Mock only _connect (device transport); assert on actual run_commands/enable calls.


def _add_cumulus_http_mocks(revision_id: str, apply_state: str = "ays"):
    """Add HTTP mocks for Cumulus commit flow. revision_id from POST; apply_state for poll."""
    base = "https://192.0.2.1:8765/nvue_v1"
    responses.add(responses.POST, f"{base}/revision", json={revision_id: {}})
    responses.add(responses.DELETE, f"{base}/", json={})
    responses.add(responses.PATCH, f"{base}/", json={})
    # Diff GETs - return data that produces "nv set interface eth0 description test"
    responses.add(
        responses.GET,
        f"{base}/?rev=applied&diff={revision_id}&filled=false",
        json={},  # removed (applied vs revision)
    )
    responses.add(
        responses.GET,
        f"{base}/?rev={revision_id}&diff=applied&filled=false",
        json={"interface": {"eth0": {"description": "test"}}},  # added
    )
    responses.add(
        responses.PATCH,
        f"{base}/revision/{revision_id}",
        json={},
    )
    responses.add(
        responses.GET,
        f"{base}/revision/{revision_id}",
        json={"state": apply_state},
    )
    responses.add(
        responses.PATCH,
        f"{base}/revision/applied",
        json={},
    )


def _get_revision_patch_bodies():
    """Extract JSON bodies of PATCH requests to revision/{id} (apply and confirm)."""
    base = "https://192.0.2.1:8765/nvue_v1/revision/"
    bodies = []
    for call in responses.calls:
        req = call.request
        if req.method != "PATCH" or not req.url.startswith(base) or "applied" in req.url:
            continue
        if req.body:
            bodies.append(json.loads(req.body))
    return bodies


@responses.activate
@patch("nv_config_manager.temporal.client.device.cumulus.time.sleep")
def test_apply_approved_configuration_cumulus_commit_confirm_true(mock_sleep):
    """With commit_confirm=True, Cumulus sends PATCH with confirm_yes/state-controls and a second confirm PATCH."""
    rev = "rev-abc"
    _add_cumulus_http_mocks(rev, apply_state="ays")

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform=Platform.CUMULUS_LINUX,
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )
    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="- set:\n  interface:\n    eth0:\n      description: test",
        approved_diff="nv set interface eth0 description test",
        commit_confirm=True,
    )

    apply_approved_configuration(activity_input)

    bodies = _get_revision_patch_bodies()
    # First PATCH = apply with commit-confirm
    assert len(bodies) >= 1
    apply_body = bodies[0]
    assert apply_body.get("auto-prompt", {}).get("confirm") == "confirm_yes"
    assert "state-controls" in apply_body
    assert apply_body["state-controls"].get("confirm") == COMMIT_CONFIRM_ROLLBACK_SECONDS
    # Second PATCH = confirm (cancel rollback)
    assert len(bodies) >= 2
    confirm_body = bodies[1]
    assert "confirm_yes" not in str(confirm_body.get("auto-prompt", {}))
    assert confirm_body.get("state") == "apply"
    assert confirm_body.get("auto-prompt", {}).get("ays") == "ays_yes"


@responses.activate
@patch("nv_config_manager.temporal.client.device.cumulus.time.sleep")
def test_apply_approved_configuration_cumulus_commit_confirm_false(mock_sleep):
    """With commit_confirm=False, Cumulus sends single PATCH without confirm_yes/state-controls, no confirm PATCH."""
    rev = "rev-xyz"
    _add_cumulus_http_mocks(rev, apply_state="applied")

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform=Platform.CUMULUS_LINUX,
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )
    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="- set:\n  interface:\n    eth0:\n      description: test",
        approved_diff="nv set interface eth0 description test",
        commit_confirm=False,
    )

    apply_approved_configuration(activity_input)

    bodies = _get_revision_patch_bodies()
    # Only one PATCH to revision (apply, no confirm)
    assert len(bodies) == 1
    apply_body = bodies[0]
    assert "confirm_yes" not in str(apply_body.get("auto-prompt", {}))
    assert "state-controls" not in apply_body
    assert apply_body.get("auto-prompt", {}).get("ays") == "ays_yes"


@patch("nv_config_manager.temporal.client.device.AristaConnection._connect")
def test_apply_approved_configuration_arista_commit_confirm_true(mock_connect):
    """With commit_confirm=True (default), Arista sends commit timer and confirm commands."""
    mock_node = MagicMock()
    mock_node.run_commands.return_value = None
    mock_node.enable.side_effect = [
        [{"result": {"output": "test diff"}}],  # _diff
        None,  # configure session X commit
        None,  # copy running-config startup-config
        [{"result": {"sessions": {}}}],  # _abort: show configuration sessions (empty => no abort)
    ]
    mock_connect.return_value = mock_node

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform=Platform.ARISTA_EOS,
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="interface Ethernet1\n description test\nend",
        approved_diff="test diff",
        commit_confirm=True,
    )

    apply_approved_configuration(activity_input)

    run_commands_calls = mock_node.run_commands.call_args_list
    commit_timer_calls = [
        c for c in run_commands_calls if c[0] and any("commit timer" in str(arg) for arg in c[0][0])
    ]
    assert len(commit_timer_calls) >= 1
    commands = commit_timer_calls[0][0][0]
    assert "commit timer 00:05:00" in commands


@patch("nv_config_manager.temporal.client.device.AristaConnection._connect")
def test_apply_approved_configuration_arista_commit_confirm_false(mock_connect):
    """With commit_confirm=False, Arista does not send commit timer; only direct commit."""
    mock_node = MagicMock()
    mock_node.run_commands.return_value = None
    mock_node.enable.side_effect = [
        [{"result": {"output": "test diff"}}],  # _diff
        None,  # configure session X commit
        None,  # copy running-config startup-config
        [{"result": {"sessions": {}}}],  # _abort: show configuration sessions (empty => no abort)
    ]
    mock_connect.return_value = mock_node

    device_data = NetworkDeviceData(
        id="c2c2b006-d4f6-4645-8ac8-a4a968050273",
        name="mock_device",
        platform=Platform.ARISTA_EOS,
        role="test_role",
        site="test_site",
        device_type="test_device_type",
        primary_ip4="192.0.2.1",
        primary_ip6=None,
    )

    activity_input = ConfigApplyActivityInput(
        device_data=device_data,
        configuration="interface Ethernet1\n description test\nend",
        approved_diff="test diff",
        commit_confirm=False,
    )

    apply_approved_configuration(activity_input)

    run_commands_calls = mock_node.run_commands.call_args_list
    commit_timer_calls = [
        c for c in run_commands_calls if c[0] and any("commit timer" in str(arg) for arg in c[0][0])
    ]
    assert len(commit_timer_calls) == 0
