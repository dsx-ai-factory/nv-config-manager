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
from configparser import ConfigParser
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from jnpr.junos.exception import (
    CommitError,
    ConfigLoadError,
    ConnectAuthError,
    ConnectClosedError,
    ConnectError,
    ProbeError,
    RpcError,
)
from lxml import etree
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.client.device import (
    ConfigSyntaxException,
    DiffChangedException,
    JuniperConnection,
    NetworkDeviceException,
)
from nv_config_manager.temporal.client.device.juniper import _junos_list


class _FakeConfigCM:
    """Stand-in for PyEZ's Config context manager wrapping a mock utility."""

    def __init__(self, cu: MagicMock) -> None:
        self._cu = cu

    def __enter__(self) -> MagicMock:
        return self._cu

    def __exit__(self, *exc: object) -> bool:
        return False


def _rpc_error_rsp(message: str, severity: str = "error") -> etree._Element:
    """Build a minimal Junos <rpc-error> element for PyEZ exception construction."""
    return etree.fromstring(
        f"<rpc-error><error-severity>{severity}</error-severity>"
        f"<error-message>{message}</error-message></rpc-error>"
    )


@pytest.fixture
def juniper_conn():
    """A JuniperConnection built with load_config patched (no network at init)."""
    config = ConfigParser()
    config.add_section("device")
    config.set("device", "username", "shooks")
    config.set("device", "password", "pw")
    with patch("nv_config_manager.temporal.client.device.base.load_config", return_value=config):
        yield JuniperConnection("192.0.2.10", password="pw")


def test_get_device_connects_once_and_caches(juniper_conn):
    """The NETCONF session is opened lazily on first use and reused afterwards."""
    fake_device = MagicMock()
    with patch(
        "nv_config_manager.temporal.client.device.juniper.Device", return_value=fake_device
    ) as mock_device:
        first = juniper_conn._get_device()
        second = juniper_conn._get_device()
    assert first is fake_device
    assert second is fake_device
    fake_device.open.assert_called_once()
    assert mock_device.call_count == 1
    assert mock_device.call_args.kwargs["port"] == 830
    assert mock_device.call_args.kwargs["conn_open_timeout"] == (
        juniper_conn._CONN_OPEN_TIMEOUT_SECONDS
    )
    assert fake_device.timeout == juniper_conn._RPC_TIMEOUT_SECONDS


def test_perform_candidate_diff_uses_config_op_timeout(juniper_conn):
    """Exclusive diff work temporarily lowers the RPC deadline, then restores it."""

    class _Device:
        def __init__(self) -> None:
            self.timeout = juniper_conn._RPC_TIMEOUT_SECONDS
            self.seen: list[int] = []

        def __setattr__(self, name: str, value: object) -> None:
            if name == "timeout":
                object.__setattr__(self, "seen", getattr(self, "seen", []) + [value])
            object.__setattr__(self, name, value)

    device = _Device()
    cu = MagicMock()
    cu.diff.return_value = "diff"
    cu.__enter__.return_value = cu
    cu.__exit__.return_value = None

    with (
        patch.object(juniper_conn, "_get_device", return_value=device),
        patch("nv_config_manager.temporal.client.device.juniper.Config", return_value=cu),
        patch.object(juniper_conn, "_load_full_config"),
    ):
        assert juniper_conn.perform_candidate_diff("system { host-name RTR1; }") == "diff"

    assert juniper_conn._CONFIG_OP_TIMEOUT_SECONDS in device.seen
    assert device.timeout == juniper_conn._RPC_TIMEOUT_SECONDS


def test_connect_rotates_then_raises_on_auth_failure(juniper_conn):
    """Genuine auth failures exhaust password rotation and raise NetworkDeviceException."""
    with patch("nv_config_manager.temporal.client.device.juniper.Device") as mock_device:
        mock_device.return_value.open.side_effect = ConnectAuthError(
            dev=SimpleNamespace(hostname="test-router")
        )
        with pytest.raises(NetworkDeviceException):
            juniper_conn._get_device()


def test_connect_raises_clear_error_on_probe_failure(juniper_conn):
    """A probe (reachability) failure raises immediately with a NETCONF-specific message."""
    with patch("nv_config_manager.temporal.client.device.juniper.Device") as mock_device:
        mock_device.return_value.open.side_effect = ProbeError(
            dev=SimpleNamespace(hostname="test-router")
        )
        with pytest.raises(NetworkDeviceException, match="NETCONF"):
            juniper_conn._get_device()


def test_close_is_safe_before_device_assigned(juniper_conn):
    """close() must not raise even if _device was never set (failed init path)."""
    del juniper_conn._device
    juniper_conn.close()


def test_context_manager_closes_session(juniper_conn):
    """Using the connection as a context manager closes the session on exit."""
    device = MagicMock()
    juniper_conn._device = device
    with juniper_conn as conn:
        assert conn is juniper_conn
    device.close.assert_called_once()
    assert juniper_conn._device is None


def test_rpc_requests_json_and_converts_flag_params(juniper_conn):
    """_rpc asks for JSON format and turns empty values into boolean flags."""
    device = MagicMock()
    device.rpc.get_interface_information.return_value = {"interface-information": []}
    with patch.object(juniper_conn, "_get_device", return_value=device):
        result = juniper_conn._rpc("get-interface-information", params={"terse": ""})
    assert result == {"interface-information": []}
    args, kwargs = device.rpc.get_interface_information.call_args
    assert args[0] == {"format": "json"}
    assert kwargs == {"terse": True}


def test_get_running_configuration_returns_text_format(juniper_conn):
    """Backup returns full hierarchical text from <configuration-output>."""
    device = MagicMock()
    device.rpc.get_config.return_value = etree.fromstring(
        "<configuration-information><configuration-output>"
        "system {\n    host-name RTR1;\n}"
        "</configuration-output></configuration-information>"
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        config = juniper_conn.get_running_configuration()
    assert config == "system {\n    host-name RTR1;\n}\n"
    assert device.rpc.get_config.call_args.kwargs["options"]["format"] == "text"


def test_get_configuration_text_returns_hierarchical(juniper_conn):
    """The text getter requests hierarchical (curly-brace) format."""
    device = MagicMock()
    device.rpc.get_config.return_value = etree.fromstring(
        "<configuration-information><configuration-output>"
        "system {\n    host-name RTR1;\n}"
        "</configuration-output></configuration-information>"
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        text = juniper_conn.get_configuration_text()
    assert "host-name RTR1" in text
    assert device.rpc.get_config.call_args.kwargs["options"]["format"] == "text"


def test_get_running_configuration_handles_unwrapped_reply_root(juniper_conn):
    """Real devices reply with <configuration-output> as the root, not nested."""
    device = MagicMock()
    device.rpc.get_config.return_value = etree.fromstring(
        "<configuration-output>system {\n    host-name RTR1;\n}</configuration-output>"
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        config = juniper_conn.get_running_configuration()
    assert config == "system {\n    host-name RTR1;\n}\n"


def test_get_running_configuration_falls_back_to_flattened_text_for_unknown_wrapper(
    juniper_conn, caplog
):
    """An unrecognized reply wrapper still yields the config via flattened text.

    A text-format reply carries nothing but the configuration body, so this is a
    safety net for wrapper shapes not seen before, instead of silently returning
    an empty backup.
    """
    device = MagicMock()
    device.rpc.get_config.return_value = etree.fromstring(
        "<data>system {\n    host-name RTR1;\n}</data>"
    )
    with (
        patch.object(juniper_conn, "_get_device", return_value=device),
        caplog.at_level("WARNING"),
    ):
        config = juniper_conn.get_running_configuration()
    assert config == "system {\n    host-name RTR1;\n}\n"
    assert "Unexpected get-configuration reply shape" in caplog.text


def test_get_running_configuration_redacts_secrets(juniper_conn):
    """get_running_configuration redacts secrets."""
    device = MagicMock()
    device.rpc.get_config.return_value = etree.fromstring(
        "<configuration-information><configuration-output>"
        "system {\n    root-authentication {\n        "
        'encrypted-password "$6$abcDE12$secretHash"; ## SECRET-DATA\n    }\n}'
        "</configuration-output></configuration-information>"
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        config = juniper_conn.get_running_configuration()
    assert "secretHash" not in config
    assert '"$6$<redacted>"' in config


def test_get_configuration_text_redacts_secrets(juniper_conn):
    """get_configuration_text redacts secrets, matching get_running_configuration."""
    device = MagicMock()
    device.rpc.get_config.return_value = etree.fromstring(
        "<configuration-information><configuration-output>"
        "system {\n    root-authentication {\n        "
        'encrypted-password "$6$abcDE12$secretHash"; ## SECRET-DATA\n    }\n}'
        "</configuration-output></configuration-information>"
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        text = juniper_conn.get_configuration_text()
    assert "secretHash" not in text
    assert '"$6$<redacted>"' in text


def test_execute_ztp_issues_zeroize_and_closes(juniper_conn):
    """execute_ztp issues request-system-zeroize and closes the NETCONF session."""
    device = MagicMock()
    juniper_conn._device = device
    with patch.object(juniper_conn, "_get_device", return_value=device):
        juniper_conn.execute_ztp()
    device.rpc.request_system_zeroize.assert_called_once_with()
    device.close.assert_called_once()
    assert juniper_conn._device is None


def test_execute_ztp_treats_dropped_session_as_success(juniper_conn):
    """A dropped NETCONF session during zeroize is treated as success."""
    device = MagicMock()
    device.rpc.request_system_zeroize.side_effect = ConnectError("session closed")
    juniper_conn._device = device
    with patch.object(juniper_conn, "_get_device", return_value=device):
        juniper_conn.execute_ztp()
    device.close.assert_called_once()
    assert juniper_conn._device is None


def test_get_ztp_status_returns_success_when_image_readable(juniper_conn):
    """get_ztp_status is success once the device answers with a running image."""
    with patch.object(juniper_conn, "get_running_image", return_value="24.4R2-S3.7-EVO"):
        assert juniper_conn.get_ztp_status() == "success"


def test_get_hostname_and_running_image_use_facts(juniper_conn):
    """Hostname and running image come from PyEZ facts."""
    device = MagicMock()
    device.facts = {"hostname": "RTR1", "version": "24.4R2-S3.7-EVO"}
    with patch.object(juniper_conn, "_get_device", return_value=device):
        assert juniper_conn.get_hostname() == "RTR1"
        assert juniper_conn.get_running_image() == "24.4R2-S3.7-EVO"


def test_get_hostname_raises_when_absent(juniper_conn):
    """A hostname still missing after a refresh on a reachable device is non-retryable."""
    device = MagicMock()
    device.facts = {"hostname": None}
    with patch.object(juniper_conn, "_get_device", return_value=device):
        with pytest.raises(ApplicationError) as excinfo:
            juniper_conn.get_hostname()
    device.facts_refresh.assert_called_once_with(keys="hostname")
    assert excinfo.value.non_retryable is True


def test_get_hostname_recovers_from_cached_none_fact(juniper_conn):
    """A None cached by an earlier failed gather is refreshed rather than reported absent."""
    device = MagicMock()
    device.facts.get.side_effect = [None, "RTR1"]
    with patch.object(juniper_conn, "_get_device", return_value=device):
        assert juniper_conn.get_hostname() == "RTR1"
    device.facts_refresh.assert_called_once_with(keys="hostname")


def test_get_running_image_recovers_from_cached_none_fact(juniper_conn):
    """The running image read recovers from a stale None the same way."""
    device = MagicMock()
    device.facts.get.side_effect = [None, "24.4R2-S3.7-EVO"]
    with patch.object(juniper_conn, "_get_device", return_value=device):
        assert juniper_conn.get_running_image() == "24.4R2-S3.7-EVO"
    device.facts_refresh.assert_called_once_with(keys="version")


def test_get_hostname_retries_when_fact_gathering_failed(juniper_conn):
    """PyEZ caches None on a failed fact read, so a dead session stays retryable."""
    device = MagicMock()
    device.facts = {"hostname": None}
    device.rpc.get_software_information.side_effect = RpcError(rsp=_rpc_error_rsp("session down"))
    with patch.object(juniper_conn, "_get_device", return_value=device):
        with pytest.raises(NetworkDeviceException) as excinfo:
            juniper_conn.get_hostname()
    assert not excinfo.value.non_retryable


def test_get_running_image_retries_when_fact_gathering_failed(juniper_conn):
    """The running image read makes the same retryable/non-retryable distinction."""
    device = MagicMock()
    device.facts = {"version": None}
    device.rpc.get_software_information.side_effect = ConnectClosedError(
        dev=SimpleNamespace(hostname="test-router")
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        with pytest.raises(NetworkDeviceException) as excinfo:
            juniper_conn.get_running_image()
    assert not excinfo.value.non_retryable


def test_get_running_image_raises_non_retryable_when_absent(juniper_conn):
    """A version the reachable device does not report is non-retryable."""
    device = MagicMock()
    device.facts = {"version": None}
    with patch.object(juniper_conn, "_get_device", return_value=device):
        with pytest.raises(NetworkDeviceException) as excinfo:
            juniper_conn.get_running_image()
    assert excinfo.value.non_retryable is True


def test_get_uptime_parses_seconds(juniper_conn):
    """Uptime is parsed from the junos:seconds attribute."""
    data = {
        "system-uptime-information": [
            {"uptime-information": [{"up-time": [{"attributes": {"junos:seconds": "12345"}}]}]}
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        assert juniper_conn.get_uptime() == 12345


def test_get_uptime_raises_on_unexpected_shape(juniper_conn):
    """Uptime raises a clear error when the response shape is unexpected."""
    with patch.object(juniper_conn, "_rpc", return_value={"unexpected": True}):
        with pytest.raises(NetworkDeviceException):
            juniper_conn.get_uptime()


def test_perform_candidate_diff_loads_full_config_rolls_back_and_returns_diff(juniper_conn):
    """perform_candidate_diff loads the full config with load update, returns the diff, discards."""
    cu = MagicMock()
    cu.diff.return_value = "[edit system]\n-  host-name OLD;\n+  host-name RTR1;"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        diff = juniper_conn.perform_candidate_diff("system { host-name RTR1; }")
    assert "host-name" in diff
    cu.load.assert_called_once_with("system { host-name RTR1; }", format="text", update=True)
    cu.rollback.assert_called_once()
    cu.commit.assert_not_called()


def test_perform_candidate_diff_rejects_partial(juniper_conn):
    """Partial diffs are rejected; no config session is opened."""
    with patch("nv_config_manager.temporal.client.device.juniper.Config") as mock_config:
        with pytest.raises(NetworkDeviceException, match="Partial configuration is not supported"):
            juniper_conn.perform_candidate_diff("system { host-name RTR1; }", partial=True)
    mock_config.assert_not_called()


def test_commit_candidate_config_rejects_partial(juniper_conn):
    """Partial commits are rejected; no config session is opened."""
    with patch("nv_config_manager.temporal.client.device.juniper.Config") as mock_config:
        with pytest.raises(NetworkDeviceException, match="Partial configuration is not supported"):
            juniper_conn.commit_candidate_config(
                "system { host-name RTR1; }", approved_diff="d", partial=True
            )
    mock_config.assert_not_called()


def test_perform_candidate_diff_raises_config_syntax_on_load_error(juniper_conn):
    """A load failure surfaces as ConfigSyntaxException and discards the candidate."""
    cu = MagicMock()
    cu.load.side_effect = ConfigLoadError(rsp=_rpc_error_rsp("syntax error"))
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        with pytest.raises(ConfigSyntaxException):
            juniper_conn.perform_candidate_diff("set nonsense")
    cu.rollback.assert_called_once()


def test_perform_candidate_diff_rolls_back_when_diff_rpc_fails(juniper_conn):
    """A mid-diff RpcError still discards the loaded candidate before unlocking."""
    cu = MagicMock()
    cu.diff.side_effect = RpcError(rsp=_rpc_error_rsp("diff failed"))
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
        patch.object(juniper_conn, "_load_full_config"),
    ):
        with pytest.raises(NetworkDeviceException, match="candidate diff"):
            juniper_conn.perform_candidate_diff("system { host-name RTR1; }")
    cu.rollback.assert_called_once()


def test_commit_candidate_config_raises_when_diff_changed(juniper_conn):
    """A mismatch between the fresh diff and the approved diff aborts before commit."""
    cu = MagicMock()
    cu.diff.return_value = "new-diff"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        with pytest.raises(DiffChangedException):
            juniper_conn.commit_candidate_config("config", approved_diff="old-diff")
    cu.rollback.assert_called_once()
    cu.commit.assert_not_called()


def test_commit_candidate_config_no_diff_confirms_pending_commit(juniper_conn):
    """Empty diff with commit_confirm still issues the confirm."""
    cu = MagicMock()
    cu.diff.return_value = ""
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.commit_candidate_config("config", approved_diff="", commit_confirm=True)
    cu.rollback.assert_called_once()
    cu.commit.assert_called_once()
    assert "confirm" not in cu.commit.call_args.kwargs


def test_commit_candidate_config_no_diff_direct_does_not_commit(juniper_conn):
    """Empty diff with commit_confirm=False issues no commit at all."""
    cu = MagicMock()
    cu.diff.return_value = ""
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.commit_candidate_config("config", approved_diff="", commit_confirm=False)
    cu.commit.assert_not_called()
    cu.rollback.assert_called_once()


def test_commit_candidate_config_commit_confirm_then_confirms(juniper_conn):
    """commit_confirm=True commits with a rollback timer then confirms with a plain commit."""
    cu = MagicMock()
    cu.diff.return_value = "diff"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.commit_candidate_config("config", approved_diff="diff", commit_confirm=True)
    # First commit carries the confirm timer; the follow-up confirm commit does not.
    assert cu.commit.call_count == 2
    assert "confirm" in cu.commit.call_args_list[0].kwargs
    assert "confirm" not in cu.commit.call_args_list[1].kwargs


def test_commit_candidate_config_direct_commit(juniper_conn):
    """commit_confirm=False commits directly with no follow-up confirm."""
    cu = MagicMock()
    cu.diff.return_value = "diff"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.commit_candidate_config("config", approved_diff="diff", commit_confirm=False)
    cu.commit.assert_called_once()
    assert "confirm" not in cu.commit.call_args.kwargs


def test_commit_candidate_config_raises_on_commit_error(juniper_conn):
    """A commit failure surfaces as NetworkDeviceException."""
    cu = MagicMock()
    cu.diff.return_value = "diff"
    cu.commit.side_effect = CommitError(rsp=_rpc_error_rsp("commit failed"))
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        with pytest.raises(NetworkDeviceException):
            juniper_conn.commit_candidate_config("config", approved_diff="diff")


# ---------------------------------------------------------------------------
# Numbered rollback and rescue configuration.
# ---------------------------------------------------------------------------


def test_get_rollback_diff_returns_diff(juniper_conn):
    """get_rollback_diff compares the active config against a numbered rollback."""
    cu = MagicMock()
    cu.diff.return_value = "rollback-diff"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        diff = juniper_conn.get_rollback_diff(3)
    assert diff == "rollback-diff"
    cu.diff.assert_called_once_with(rb_id=3)
    cu.rollback.assert_called_once()


def test_rollback_configuration_commits_when_diff(juniper_conn):
    """rollback_configuration loads the numbered revision and commits when it differs."""
    cu = MagicMock()
    cu.diff.return_value = "diff"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.rollback_configuration(2, commit_confirm=False)
    cu.rollback.assert_called_once_with(rb_id=2)
    cu.commit.assert_called_once()


def test_rollback_configuration_noop_when_no_diff(juniper_conn):
    """When the numbered revision matches the active config, nothing is committed."""
    cu = MagicMock()
    cu.diff.return_value = ""
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.rollback_configuration(2, commit_confirm=False)
    # rollback(rb_id=2) to load, then rollback() to discard the unchanged candidate.
    assert cu.rollback.call_count == 2
    cu.commit.assert_not_called()


def test_save_rescue_configuration_calls_rescue_save(juniper_conn):
    """save_rescue_configuration issues a rescue save."""
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch("nv_config_manager.temporal.client.device.juniper.Config") as mock_config,
    ):
        juniper_conn.save_rescue_configuration()
    mock_config.return_value.rescue.assert_called_once_with(action="save")


def test_get_rescue_configuration_returns_text(juniper_conn):
    """get_rescue_configuration reads get-rescue-information directly."""
    device = MagicMock()
    device.rpc.get_rescue_information.return_value = etree.fromstring(
        "<rescue-information><configuration-information><configuration-output>"
        "system { host-name RTR1; }"
        "</configuration-output></configuration-information></rescue-information>"
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        rescue = juniper_conn.get_rescue_configuration()
    assert rescue == "system { host-name RTR1; }"
    device.rpc.get_rescue_information.assert_called_once_with(format="text")


def test_get_rescue_configuration_returns_none_when_absent(juniper_conn):
    """Missing rescue is reported as an rpc-error and mapped to None."""
    device = MagicMock()
    device.rpc.get_rescue_information.side_effect = RpcError(
        rsp=_rpc_error_rsp("Rescue configuration does not exist")
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        assert juniper_conn.get_rescue_configuration() is None


def test_get_rescue_configuration_raises_on_transport_error(juniper_conn):
    """Transport failures are not silently treated as a missing rescue config."""
    device = MagicMock()
    device.rpc.get_rescue_information.side_effect = ConnectError(
        dev=SimpleNamespace(hostname="test-router")
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        with pytest.raises(NetworkDeviceException, match="rescue configuration"):
            juniper_conn.get_rescue_configuration()


def test_get_rescue_configuration_raises_on_unexpected_rpc_error(juniper_conn):
    """Non-absent rescue RpcErrors surface instead of looking like None."""
    device = MagicMock()
    device.rpc.get_rescue_information.side_effect = RpcError(
        rsp=_rpc_error_rsp("permission denied")
    )
    with patch.object(juniper_conn, "_get_device", return_value=device):
        with pytest.raises(NetworkDeviceException, match="rescue configuration"):
            juniper_conn.get_rescue_configuration()


def test_delete_rescue_configuration_calls_rescue_delete(juniper_conn):
    """delete_rescue_configuration issues a rescue delete."""
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch("nv_config_manager.temporal.client.device.juniper.Config") as mock_config,
    ):
        juniper_conn.delete_rescue_configuration()
    mock_config.return_value.rescue.assert_called_once_with(action="delete")


def test_rollback_to_rescue_reloads_and_commits(juniper_conn):
    """rollback_to_rescue reloads the rescue config and commits when it differs."""
    cu = MagicMock()
    cu.rescue.return_value = True
    cu.diff.return_value = "diff"
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.rollback_to_rescue(commit_confirm=False)
    cu.rescue.assert_called_once_with(action="reload")
    cu.commit.assert_called_once()


def test_rollback_to_rescue_noop_when_no_diff(juniper_conn):
    """rollback_to_rescue does nothing when the rescue config matches the active config."""
    cu = MagicMock()
    cu.rescue.return_value = True
    cu.diff.return_value = ""
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        juniper_conn.rollback_to_rescue(commit_confirm=False)
    cu.commit.assert_not_called()
    cu.rollback.assert_called_once()


def test_rollback_to_rescue_raises_when_rescue_missing(juniper_conn):
    """PyEZ rescue reload returns False when no rescue exists; surface that clearly."""
    cu = MagicMock()
    cu.rescue.return_value = False
    with (
        patch.object(juniper_conn, "_get_device", return_value=MagicMock()),
        patch(
            "nv_config_manager.temporal.client.device.juniper.Config",
            return_value=_FakeConfigCM(cu),
        ),
    ):
        with pytest.raises(NetworkDeviceException, match="No rescue configuration"):
            juniper_conn.rollback_to_rescue(commit_confirm=False)
    cu.rollback.assert_called_once()
    cu.commit.assert_not_called()


def test_run_diagnostic_command_dispatches_supported_junos_command(juniper_conn):
    """A supported Junos diagnostic maps to its RPC and serialises to JSON."""
    with patch.object(juniper_conn, "_rpc", return_value={"host-name": "test-router"}) as mock_rpc:
        raw = juniper_conn.run_diagnostic_command("show_version")
    mock_rpc.assert_called_once_with("get-software-information")
    assert json.loads(raw) == {"host-name": "test-router"}


def test_run_diagnostic_command_unsupported_junos_raises_network_exception(juniper_conn):
    """Junos diagnostics with no RPC mapping surface a NetworkDeviceException, not
    a raw NotImplementedError from the base stub."""
    with pytest.raises(NetworkDeviceException, match="not implemented for JuniperConnection"):
        juniper_conn.run_diagnostic_command("show_bgp_summary")


class TestJunosList:
    """Tests for the _junos_list Junos-JSON normalization helper."""

    def test_missing_key_returns_empty_list(self):
        """A key absent from the container returns an empty list, not an error."""
        assert _junos_list({}, "mac-table-entry") == []

    def test_wraps_a_bare_dict_as_a_single_item_list(self):
        """Junos omits the list wrapper entirely when there is exactly one element."""
        entry = {"mac-address": [{"data": "00:11:22:33:44:55"}]}
        assert _junos_list({"mac-table-entry": entry}, "mac-table-entry") == [entry]

    def test_preserves_an_actual_list(self):
        """A real list of entries is returned as-is."""
        entries = [{"a": 1}, {"b": 2}]
        assert _junos_list({"mac-table-entry": entries}, "mac-table-entry") == entries

    @pytest.mark.parametrize("scalar", ["some-string", 5, 1.5, True])
    def test_scalar_value_returns_empty_list_instead_of_iterating_it(self, scalar):
        """A stray string/number under a repeatable key must not be iterated character-by-character."""
        assert _junos_list({"mac-table-entry": scalar}, "mac-table-entry") == []


def _lldp_neighbor_entry(local_port: str, remote_port: str, remote_system: str) -> dict:
    """Build one lldp-neighbor-information entry as PyEZ returns it in JSON."""
    return {
        "lldp-local-port-id": [{"data": local_port}],
        "lldp-remote-port-id": [{"data": remote_port}],
        "lldp-remote-system-name": [{"data": remote_system}],
    }


def test_get_lldp_data_returns_neighbor_for_matching_interface(juniper_conn):
    """get_lldp_data finds the single neighbor entry for the requested local port."""
    data = {
        "lldp-neighbors-information": [
            {
                "lldp-neighbor-information": [
                    _lldp_neighbor_entry("ge-0/0/0", "et-0/0/1", "junos-backbone-vjunos02"),
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        result = juniper_conn.get_lldp_data("ge-0/0/0")
    assert result.device_name == "junos-backbone-vjunos02"
    assert result.name == "et-0/0/1"


def test_get_lldp_data_returns_none_when_no_match(juniper_conn):
    """get_lldp_data returns None when the interface has no LLDP neighbor."""
    data = {"lldp-neighbors-information": [{"lldp-neighbor-information": []}]}
    with patch.object(juniper_conn, "_rpc", return_value=data):
        assert juniper_conn.get_lldp_data("ge-0/0/5") is None


def test_get_lldp_data_raises_on_multiple_neighbors_for_one_interface(juniper_conn):
    """Multiple neighbors on the same local port is treated as ambiguous, like Arista."""
    data = {
        "lldp-neighbors-information": [
            {
                "lldp-neighbor-information": [
                    _lldp_neighbor_entry("ge-0/0/0", "et-0/0/1", "device-a"),
                    _lldp_neighbor_entry("ge-0/0/0", "et-0/0/2", "device-b"),
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        with pytest.raises(NetworkDeviceException, match="multiple LLDP neighbors"):
            juniper_conn.get_lldp_data("ge-0/0/0")


def test_get_interface_connections_combines_lldp_and_link_state(juniper_conn):
    """get_interface_connections merges LLDP neighbors with per-interface link state."""
    lldp_data = {
        "lldp-neighbors-information": [
            {
                "lldp-neighbor-information": [
                    _lldp_neighbor_entry("ge-0/0/0", "et-0/0/1", "junos-backbone-vjunos02"),
                ]
            }
        ]
    }
    link_state_data = {
        "interface-information": [
            {
                "physical-interface": [
                    {"name": [{"data": "ge-0/0/0"}], "oper-status": [{"data": "up"}]},
                    {"name": [{"data": "ge-0/0/1"}], "oper-status": [{"data": "down"}]},
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", side_effect=[lldp_data, link_state_data]):
        result = juniper_conn.get_interface_connections()
    assert result.neighbors["ge-0/0/0"].device_name == "junos-backbone-vjunos02"
    assert result.link_states == {"ge-0/0/0": True, "ge-0/0/1": False}


def test_get_interface_connections_handles_no_neighbors(juniper_conn):
    """An empty LLDP table still returns link states with no neighbors."""
    empty_lldp = {"lldp-neighbors-information": [{"lldp-neighbor-information": []}]}
    link_state_data = {
        "interface-information": [
            {
                "physical-interface": [
                    {"name": [{"data": "ge-0/0/0"}], "oper-status": [{"data": "up"}]}
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", side_effect=[empty_lldp, link_state_data]):
        result = juniper_conn.get_interface_connections()
    assert result.neighbors == {}
    assert result.link_states == {"ge-0/0/0": True}


def test_get_interface_connections_raises_on_multiple_neighbors_for_one_interface(juniper_conn):
    """A hub/fan-in on one port must raise here too, not silently keep the last neighbor."""
    lldp_data = {
        "lldp-neighbors-information": [
            {
                "lldp-neighbor-information": [
                    _lldp_neighbor_entry("ge-0/0/0", "et-0/0/1", "device-a"),
                    _lldp_neighbor_entry("ge-0/0/0", "et-0/0/2", "device-b"),
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=lldp_data):
        with pytest.raises(NetworkDeviceException, match="multiple LLDP neighbors"):
            juniper_conn.get_interface_connections()


def test_get_mac_table_parses_switching_table_entries(juniper_conn):
    """get_mac_table parses mac-table-entry rows, keyed by physical interface."""
    data = {
        "ethernet-switching-table-information": [
            {
                "ethernet-switching-table": [
                    {
                        "mac-table-entry": [
                            {
                                "mac-address": [{"data": "00:11:22:33:44:55"}],
                                "mac-interfaces-list": [{"data": "ge-0/0/0.0"}],
                                "mac-vlan": [{"data": "100"}],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        result = juniper_conn.get_mac_table()
    mac = "00-11-22-33-44-55"
    assert result.by_mac[mac].interface == "ge-0/0/0"
    assert result.by_mac[mac].vlan == 100
    assert result.by_interface["ge-0/0/0"] == [mac]


def test_get_mac_table_skips_entry_with_invalid_mac(juniper_conn):
    """A malformed MAC in one entry is skipped rather than aborting the whole table."""
    data = {
        "ethernet-switching-table-information": [
            {
                "ethernet-switching-table": [
                    {
                        "mac-table-entry": [
                            {
                                "mac-address": [{"data": "not-a-mac"}],
                                "mac-interfaces-list": [{"data": "ge-0/0/0.0"}],
                            },
                            {
                                "mac-address": [{"data": "00:11:22:33:44:55"}],
                                "mac-interfaces-list": [{"data": "ge-0/0/1.0"}],
                            },
                        ]
                    }
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        result = juniper_conn.get_mac_table()
    assert list(result.by_mac.keys()) == ["00-11-22-33-44-55"]
    assert result.by_interface == {"ge-0/0/1": ["00-11-22-33-44-55"]}


def _raise_unsupported_switching_table(*_args: object, **_kwargs: object) -> None:
    """Raise the RpcError Junos actually returns for a backbone router with no bridging."""
    cause = Exception()
    cause.message = "the l2-learning subsystem is not running"  # matches jnpr RpcError.message
    raise NetworkDeviceException("RPC get-ethernet-switching-table-information failed") from cause


def test_get_mac_table_returns_empty_when_switching_unsupported(juniper_conn):
    """A backbone router without bridging rejects the RPC; treat that as an empty table."""
    with patch.object(juniper_conn, "_rpc", side_effect=_raise_unsupported_switching_table):
        result = juniper_conn.get_mac_table()
    assert result.by_mac == {}
    assert result.by_interface == {}


def test_get_arp_table_parses_entries_and_strips_logical_unit(juniper_conn):
    """get_arp_table maps IP/MAC/interface, keying interfaces by physical name."""
    data = {
        "arp-table-information": [
            {
                "arp-table-entry": [
                    {
                        "mac-address": [{"data": "00:11:22:33:44:55"}],
                        "ip-address": [{"data": "192.0.2.1"}],
                        "interface-name": [{"data": "ge-0/0/0.0"}],
                    }
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        result = juniper_conn.get_arp_table()
    mac = "00-11-22-33-44-55"
    assert result.ip_to_mac["192.0.2.1"] == [mac]
    assert result.mac_to_ip[mac] == ["192.0.2.1"]
    assert result.interface_to_mac["ge-0/0/0"] == [mac]


def test_get_arp_table_skips_incomplete_entries(juniper_conn):
    """Entries missing a mac, ip, or interface are skipped rather than raising."""
    data = {
        "arp-table-information": [
            {
                "arp-table-entry": [
                    {"mac-address": [{"data": "00:11:22:33:44:55"}], "ip-address": []},
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        result = juniper_conn.get_arp_table()
    assert result.ip_to_mac == {}
    assert result.mac_to_ip == {}


def test_get_arp_table_skips_entries_with_invalid_ip_or_mac(juniper_conn):
    """A malformed IP or MAC in one entry is skipped rather than aborting the whole table."""
    data = {
        "arp-table-information": [
            {
                "arp-table-entry": [
                    {
                        "ip-address": [{"data": "not-an-ip"}],
                        "mac-address": [{"data": "00:11:22:33:44:55"}],
                        "interface-name": [{"data": "ge-0/0/0.0"}],
                    },
                    {
                        "ip-address": [{"data": "10.0.0.1"}],
                        "mac-address": [{"data": "not-a-mac"}],
                        "interface-name": [{"data": "ge-0/0/0.0"}],
                    },
                    {
                        "ip-address": [{"data": "10.0.0.2"}],
                        "mac-address": [{"data": "00:11:22:33:44:66"}],
                        "interface-name": [{"data": "ge-0/0/0.0"}],
                    },
                ]
            }
        ]
    }
    with patch.object(juniper_conn, "_rpc", return_value=data):
        result = juniper_conn.get_arp_table()
    assert result.ip_to_mac == {"10.0.0.2": ["00-11-22-33-44-66"]}
    assert result.mac_to_ip == {"00-11-22-33-44-66": ["10.0.0.2"]}


def test_get_arp_table_returns_empty_for_empty_reply(juniper_conn):
    """An empty arp-table-information reply produces an empty table, not an error."""
    with patch.object(juniper_conn, "_rpc", return_value={"arp-table-information": []}):
        result = juniper_conn.get_arp_table()
    assert result.ip_to_mac == {}
