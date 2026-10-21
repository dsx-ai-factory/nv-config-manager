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
"""Tests for the NETCONF-backed IOS XR session against a fake ncclient manager (no router)."""

from unittest.mock import MagicMock

import pytest
from lxml import etree  # type: ignore[import-untyped]  # ty: ignore[unresolved-import]
from ncclient.operations import RPCError  # type: ignore[import-untyped]
from ncclient.transport.errors import SSHError  # type: ignore[import-untyped]

from nv_config_manager_workflows.clients.device.cisco_xr import (
    _CLI_CFG_NS,
    _DIFF_NS,
    _NETCONF_BASE_NS,
    NetconfIosXrSession,
)
from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigSyntaxException,
    NetworkDeviceException,
)

TEXT = "hostname xr-router-1\ninterface Loopback0\n description x\n!\nend\n"
DIFF = "!! IOS XR Configuration 26.4.1\n+  interface Loopback0\nend\n"


class _RpcError(RPCError):  # type: ignore[misc]
    """A real RPCError subclass with the fields the session reads."""

    def __init__(self, message: str, tag: str = "operation-failed") -> None:
        Exception.__init__(self, message)
        self._message = message
        self._tag = tag

    @property
    def message(self) -> str:
        return self._message

    @property
    def tag(self) -> str:
        return self._tag


def _running_reply(text: str) -> MagicMock:
    data = etree.Element("data")
    etree.SubElement(data, f"{{{_CLI_CFG_NS}}}cli", nsmap={None: _CLI_CFG_NS}).text = text
    reply = MagicMock()
    reply.xml = etree.tostring(data, encoding="unicode")
    return reply


def _diff_reply(text: str) -> str:
    reply = etree.Element("rpc-reply")
    etree.SubElement(reply, f"{{{_DIFF_NS}}}response", nsmap={None: _DIFF_NS}).text = text
    return str(etree.tostring(reply, encoding="unicode"))


def _session() -> tuple[NetconfIosXrSession, MagicMock]:
    manager = MagicMock()
    manager.timeout = 30
    manager.dispatch.return_value.xml = _diff_reply(DIFF)
    return NetconfIosXrSession(manager), manager


def test_show_running_config_reads_the_cli_leaf_with_get_config() -> None:
    session, manager = _session()
    manager.get_config.return_value = _running_reply("hostname r1\n")

    assert session.show("show running-config", read_timeout=12) == "hostname r1\n"

    kwargs = manager.get_config.call_args.kwargs
    assert kwargs["source"] == "running"
    assert kwargs["filter"].find(f"{{{_CLI_CFG_NS}}}cli") is not None
    # the per-call timeout is applied for the RPC and restored afterwards
    assert manager.timeout == 30


def test_show_running_config_maps_rpc_and_transport_errors() -> None:
    session, manager = _session()
    manager.get_config.side_effect = _RpcError("bad request")
    with pytest.raises(NetworkDeviceException, match="bad request"):
        session.show("show running-config")

    manager.get_config.side_effect = SSHError("reset")
    with pytest.raises(NetworkDeviceException, match="reset"):
        session.show("show running-config")


def test_an_unsupported_cli_leaf_is_a_non_retryable_error_names_the_cli_leaf() -> None:
    session, manager = _session()
    manager.get_config.side_effect = _RpcError("unknown element", tag="unknown-element")

    with pytest.raises(NetworkDeviceException, match="cli-cfg") as raised:
        session.show("show running-config")

    assert raised.value.non_retryable


@pytest.mark.parametrize("command", ["show version", "show lldp neighbors detail", "show clock"])
def test_other_show_commands_have_no_netconf_path_yet(command: str) -> None:
    session, manager = _session()

    with pytest.raises(NetworkDeviceException, match="no NETCONF path") as raised:
        session.show(command)

    assert raised.value.non_retryable
    manager.get_config.assert_not_called()
    manager.dispatch.assert_not_called()


def test_get_sends_the_subtree_filter_and_returns_the_reply_xml() -> None:
    session, manager = _session()
    manager.get.return_value = MagicMock(xml="<rpc-reply/>")
    subtree = (
        '<install xmlns="http://cisco.com/ns/yang/Cisco-IOS-XR-install-oper"><version/></install>'
    )

    assert session.get(subtree, read_timeout=12) == "<rpc-reply/>"

    selector = manager.get.call_args.kwargs["filter"]
    assert selector.tag.endswith("}filter")
    assert selector.find("{http://cisco.com/ns/yang/Cisco-IOS-XR-install-oper}install") is not None
    assert manager.timeout == 30  # the per-call timeout is restored


def test_get_maps_rpc_and_transport_errors() -> None:
    session, manager = _session()
    manager.get.side_effect = _RpcError("bad filter")
    with pytest.raises(NetworkDeviceException, match="bad filter"):
        session.get("<x/>")

    manager.get.side_effect = SSHError("reset")
    with pytest.raises(NetworkDeviceException, match="reset"):
        session.get("<x/>")


def test_load_replacement_locks_copies_the_text_onto_the_candidate_and_returns_the_diff() -> None:
    session, manager = _session()

    diff = session.load_replacement(TEXT, timeout=45)

    assert diff == DIFF
    manager.lock.assert_called_once_with("candidate")
    manager.discard_changes.assert_called_once_with()
    kwargs = manager.copy_config.call_args.kwargs
    assert kwargs["target"] == "candidate"
    source = etree.fromstring(kwargs["source"])
    assert source.tag == f"{{{_NETCONF_BASE_NS}}}source"
    leaf = source.find(f"{{{_NETCONF_BASE_NS}}}config").find(f"{{{_CLI_CFG_NS}}}cli")
    assert leaf.text == TEXT
    # the diff is the router's get-cli-config-diff action
    assert manager.dispatch.call_args.args[0].tag == f"{{{_DIFF_NS}}}get-cli-config-diff"
    # the candidate stays locked and loaded for the commit
    manager.unlock.assert_not_called()
    manager.edit_config.assert_not_called()


def test_the_copy_runs_under_the_given_timeout_and_the_session_timeout_is_restored() -> None:
    session, manager = _session()
    seen: list[float] = []
    manager.copy_config.side_effect = lambda **_: seen.append(manager.timeout)

    session.load_replacement(TEXT, timeout=45)

    assert seen == [45]
    assert manager.timeout == 30


def test_candidate_diff_returns_the_diff_and_leaves_nothing_loaded() -> None:
    session, manager = _session()

    assert session.candidate_diff(TEXT, timeout=45) == DIFF

    manager.commit.assert_not_called()
    manager.discard_changes.assert_called()
    manager.unlock.assert_called_once_with("candidate")


def test_a_rejected_configuration_is_discarded_and_never_committed() -> None:
    session, manager = _session()
    manager.copy_config.side_effect = _RpcError("Invalid input detected at '^' marker")

    with pytest.raises(ConfigSyntaxException, match="Invalid input"):
        session.load_replacement(TEXT, timeout=45)

    manager.commit.assert_not_called()
    manager.discard_changes.assert_called()
    manager.unlock.assert_called_once_with("candidate")


def test_a_router_that_rejects_copy_config_is_a_non_retryable_error_naming_the_cli_leaf() -> None:
    session, manager = _session()
    manager.copy_config.side_effect = _RpcError("not supported", tag="operation-not-supported")

    with pytest.raises(NetworkDeviceException, match="cli") as raised:
        session.load_replacement(TEXT, timeout=45)

    assert raised.value.non_retryable
    manager.unlock.assert_called_once_with("candidate")


def test_a_failed_diff_action_discards_and_unlocks() -> None:
    session, manager = _session()
    manager.dispatch.side_effect = _RpcError("diff failed")

    with pytest.raises(NetworkDeviceException, match="get-cli-config-diff"):
        session.load_replacement(TEXT, timeout=45)

    manager.unlock.assert_called_once_with("candidate")


def test_a_dropped_session_during_the_copy_is_a_retryable_error_and_cleans_up() -> None:
    session, manager = _session()
    manager.copy_config.side_effect = SSHError("session dropped")

    with pytest.raises(NetworkDeviceException, match="Failed to load") as raised:
        session.load_replacement(TEXT, timeout=45)

    assert not raised.value.non_retryable
    manager.unlock.assert_called_once_with("candidate")


def test_a_locked_candidate_is_a_clear_error_and_loads_nothing() -> None:
    session, manager = _session()
    manager.lock.side_effect = _RpcError("lock-denied", tag="lock-denied")

    with pytest.raises(NetworkDeviceException, match="lock"):
        session.load_replacement(TEXT, timeout=45)

    manager.copy_config.assert_not_called()


def test_commit_with_timer_commits_confirmed_in_seconds_and_keeps_the_lock() -> None:
    session, manager = _session()

    session.commit(confirm_seconds=300, timeout=120)

    manager.commit.assert_called_once_with(confirmed=True, timeout="300")
    # the trial commit keeps the lock so the same session confirms it
    manager.unlock.assert_not_called()


def test_commit_without_timer_commits_plainly_and_releases_the_lock() -> None:
    session, manager = _session()

    session.commit(confirm_seconds=None, timeout=120)

    manager.commit.assert_called_once_with()
    manager.unlock.assert_called_once_with("candidate")


def test_a_failed_commit_is_discarded_and_reported() -> None:
    session, manager = _session()
    manager.commit.side_effect = _RpcError("Failed to commit one or more configuration items")

    with pytest.raises(NetworkDeviceException, match="Commit failed"):
        session.commit(confirm_seconds=300, timeout=120)

    manager.unlock.assert_called_once_with("candidate")
    assert manager.timeout == 30


def test_remaining_diff_copies_again_and_keeps_the_lock_for_the_confirm() -> None:
    session, manager = _session()

    assert session.remaining_diff(TEXT, timeout=120) == DIFF

    manager.copy_config.assert_called_once()
    manager.discard_changes.assert_called_once_with()
    manager.unlock.assert_not_called()


def test_discard_releases_the_lock_and_never_raises() -> None:
    session, manager = _session()
    manager.discard_changes.side_effect = SSHError("gone")
    manager.unlock.side_effect = SSHError("gone")

    session.discard()

    manager.discard_changes.assert_called_once_with()
    manager.unlock.assert_called_once_with("candidate")


def test_confirm_sends_a_confirming_commit_and_releases_the_lock() -> None:
    session, manager = _session()

    session.confirm()

    manager.commit.assert_called_once_with()
    manager.unlock.assert_called_once_with("candidate")


def test_a_failed_confirm_is_an_error() -> None:
    session, manager = _session()
    manager.commit.side_effect = SSHError("session dropped")

    with pytest.raises(NetworkDeviceException, match="confirm"):
        session.confirm()


def _action_xml(manager: MagicMock) -> tuple[str, dict[str, str]]:
    rpc = manager.dispatch.call_args.args[0]
    return rpc.tag, {child.tag.split("}")[1]: child.text for child in rpc}


def test_rollback_last_calls_the_rollback_action_with_the_count() -> None:
    session, manager = _session()
    manager.dispatch.return_value = MagicMock(xml="<rpc-reply/>")

    session.rollback("last 2")

    tag, params = _action_xml(manager)
    assert (
        tag
        == "{http://cisco.com/ns/yang/Cisco-IOS-XR-cfgmgr-rollback-act}roll-back-configuration-last"
    )
    assert params == {"count": "2"}


def test_rollback_to_a_commit_id_calls_the_other_action() -> None:
    session, manager = _session()
    manager.dispatch.return_value = MagicMock(xml="<rpc-reply/>")

    session.rollback("1000000016")

    tag, params = _action_xml(manager)
    assert tag.endswith("}roll-back-configuration-to")
    assert params == {"commit-id": "1000000016"}


def test_action_maps_a_refusal_to_a_non_retryable_error_and_a_drop_to_a_retryable_one() -> None:
    session, manager = _session()
    manager.dispatch.side_effect = _RpcError("no such commit")
    with pytest.raises(NetworkDeviceException, match="no such commit") as refused:
        session.rollback("last 1")
    assert refused.value.non_retryable

    manager.dispatch.side_effect = SSHError("reset")
    with pytest.raises(NetworkDeviceException, match="reset") as dropped:
        session.action("urn:x", "do-it", read_timeout=5)
    assert not dropped.value.non_retryable
    assert manager.timeout == 30
