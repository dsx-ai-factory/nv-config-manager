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
"""Tests for the IB port GUID discovery + sync activities."""

from __future__ import annotations

from configparser import ConfigParser
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_guid_discovery import (
    IB_GUID_CF_KEY,
    IBGuidMapping,
    SyncIBGuidInput,
    _compute_guid_mappings,
    _intended_neighbor_from_interface,
    _neighbors_for_switch_device,
    sync_ib_guid_on_interface,
)

NB_URL = "https://nautobot.example.com"
NB_API = f"{NB_URL}/api"

SWITCH_ID = "sw-uuid-1"
SWITCH_NAME = "ib-leaf-01"
COMPUTE_DEVICE = "compute-01"
COMPUTE_IFACE = "mlx5_0"
INTERFACE_ID = "iface-uuid-1"

GUID_A = "0x0002c903000a0a01"
GUID_B = "0x0002c903000a0a02"

NB_IFACE_NAMES = {
    "name": "HCA-5/1",
    "device": {"display": "dgx-03", "name": "dgx-03"},
}


def _nb_config() -> ConfigParser:
    config = ConfigParser()
    config.add_section("dcim")
    config.set("dcim", "provider", "nautobot-2x")
    config.set("dcim", "server", NB_URL)
    config.set("dcim", "token", "test-token")
    config.set("dcim", "verify", "false")
    config.add_section("nats")
    return config


@pytest.fixture(autouse=True)
def reset_secrets_cache():
    clear_secrets_cache()
    yield
    clear_secrets_cache()


@pytest.fixture()
def mock_nb_config():
    with patch("nv_config_manager.common.config.load_config", return_value=_nb_config()):
        yield


def _make_ib_port(
    *,
    guid: str,
    peer_switch: str = SWITCH_NAME,
    peer_port: str = "10",
    system_name: str = "mlx5-system-01",
    port: str = "1",
) -> dict[str, str]:
    return {
        "system_name": system_name,
        "port": port,
        "guid": guid,
        "peer_node_name": peer_switch,
        "peer_port": peer_port,
    }


def _neighbors_for(switch_id: str, *, compute=COMPUTE_DEVICE, iface=COMPUTE_IFACE, port="10"):
    return {switch_id: {port: {"device_name": compute, "name": iface}}}


def _nb_iface_cache(ib_guid: str = "") -> dict:
    return {
        (COMPUTE_DEVICE.lower(), COMPUTE_IFACE): {"id": INTERFACE_ID, "ib_guid": ib_guid},
    }


# ---------------------------------------------------------------------------
# IBGuidMapping._classify_action
# ---------------------------------------------------------------------------


class TestClassifyAction:
    def test_empty_desired_skips(self):
        assert IBGuidMapping._classify_action("anything", "") == "skip"

    def test_noop_when_equal_case_insensitive(self):
        assert IBGuidMapping._classify_action(GUID_A, GUID_A.upper()) == "noop"

    def test_set_when_current_empty(self):
        assert IBGuidMapping._classify_action("", GUID_A) == "set"

    def test_update_when_different(self):
        assert IBGuidMapping._classify_action(GUID_A, GUID_B) == "update"


# ---------------------------------------------------------------------------
# GraphQL neighbor parsing (_fetch_switches_and_intended_neighbors helpers)
# ---------------------------------------------------------------------------


class TestIntendedNeighborFromInterface:
    def test_none_when_not_connected(self):
        assert _intended_neighbor_from_interface({"name": "1/1"}) is None

    def test_none_when_far_end_incomplete(self):
        iface = {
            "name": "swp1",
            "connected_interface": {"name": "", "device": {"name": "host"}},
        }
        assert _intended_neighbor_from_interface(iface) is None

    def test_parses_connected(self):
        iface = {
            "name": "swp1s1",
            "connected_interface": {
                "name": "eth0",
                "device": {"name": "compute-01"},
            },
        }
        assert _intended_neighbor_from_interface(iface) == (
            "swp1s1",
            {"device_name": "compute-01", "name": "eth0"},
        )


class TestNeighborsForSwitchDevice:
    def test_multiple_connected_interfaces(self):
        """Regression: inner loop must not shadow the device dict (multi-port)."""
        device = {
            "interfaces": [
                {
                    "name": "1",
                    "connected_interface": {"name": "a", "device": {"name": "H1"}},
                },
                {
                    "name": "2",
                    "connected_interface": {"name": "b", "device": {"name": "H2"}},
                },
            ]
        }
        assert _neighbors_for_switch_device(device) == {
            "1": {"device_name": "H1", "name": "a"},
            "2": {"device_name": "H2", "name": "b"},
        }


# ---------------------------------------------------------------------------
# _compute_guid_mappings (the pure join)
# ---------------------------------------------------------------------------


class TestComputeGuidMappings:
    def test_happy_path_set(self):
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid=GUID_A)],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(ib_guid=""),
        )
        assert len(mappings) == 1
        m = mappings[0]
        assert m.action == "set"
        assert m.device_name == COMPUTE_DEVICE
        assert m.interface_name == COMPUTE_IFACE
        assert m.interface_id == INTERFACE_ID
        assert m.discovered_guid == GUID_A
        assert m.current_guid == ""

    def test_noop_when_guid_already_correct(self):
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid=GUID_A)],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(ib_guid=GUID_A),
        )
        assert [m.action for m in mappings] == ["noop"]

    def test_update_when_guid_differs(self):
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid=GUID_A)],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(ib_guid="0xDEADBEEF"),
        )
        assert [m.action for m in mappings] == ["update"]
        assert mappings[0].current_guid == "0xDEADBEEF"

    def test_skips_switch_side_ports(self):
        """A port whose system_name is a managed switch is a switch-side entry
        and must never be synced."""
        mappings = _compute_guid_mappings(
            ib_ports=[
                _make_ib_port(
                    guid=GUID_A,
                    system_name=SWITCH_NAME,
                    peer_switch="some-other-host",
                )
            ],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(),
        )
        assert mappings == []

    def test_skips_when_peer_is_unmanaged_switch(self):
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid=GUID_A, peer_switch="unknown-switch")],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(),
        )
        assert mappings == []

    def test_skips_with_reason_when_no_cable_modeled(self):
        """Peer switch is managed but that switch port has no intended neighbor
        -> mapping reported with action=skip and a reason."""
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid=GUID_A, peer_port="99")],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(),
        )
        assert [m.action for m in mappings] == ["skip"]
        assert "No cable modeled" in mappings[0].reason

    def test_skips_when_nautobot_iface_missing(self):
        """Intended neighbor resolves a (device, iface) pair that doesn't exist in
        the Nautobot cache -> skip with reason (not a crash)."""
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid=GUID_A)],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface={},  # empty cache
        )
        assert [m.action for m in mappings] == ["skip"]
        assert "not found" in mappings[0].reason

    def test_skips_ports_with_no_guid(self):
        mappings = _compute_guid_mappings(
            ib_ports=[_make_ib_port(guid="")],
            switch_id_to_name={SWITCH_ID: SWITCH_NAME},
            neighbors_by_switch_id=_neighbors_for(SWITCH_ID),
            nautobot_interface_by_dev_iface=_nb_iface_cache(),
        )
        assert mappings == []


# ---------------------------------------------------------------------------
# sync_ib_guid_on_interface
# ---------------------------------------------------------------------------


class TestSyncIBGuidOnInterface:
    @pytest.mark.asyncio
    async def test_rejects_missing_interface_id(self, mock_nb_config):
        with pytest.raises(ApplicationError, match="interface_id is required"):
            await sync_ib_guid_on_interface(
                SyncIBGuidInput(interface_id="", guid=GUID_A, dry_run=False)
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_guid(self, mock_nb_config):
        with pytest.raises(ApplicationError, match="guid is required"):
            await sync_ib_guid_on_interface(
                SyncIBGuidInput(interface_id=INTERFACE_ID, guid="", dry_run=False)
            )

    @pytest.mark.asyncio
    async def test_dry_run_does_not_patch(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                f"{NB_API}/dcim/interfaces/{INTERFACE_ID}/",
                payload={
                    "id": INTERFACE_ID,
                    **NB_IFACE_NAMES,
                    "custom_fields": {IB_GUID_CF_KEY: ""},
                },
            )

            result = await sync_ib_guid_on_interface(
                SyncIBGuidInput(interface_id=INTERFACE_ID, guid=GUID_A, dry_run=True)
            )

            # No PATCH should have been issued.
            patch_calls = [(k, v) for k, v in m.requests.items() if k[0].lower() == "patch"]
            assert patch_calls == []

        assert result.dry_run is True
        assert result.changed is False
        assert result.previous_guid == ""
        assert result.new_guid == GUID_A
        assert result.device_name == "dgx-03"
        assert result.interface_name == "HCA-5/1"

    @pytest.mark.asyncio
    async def test_skips_when_current_equals_desired(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                f"{NB_API}/dcim/interfaces/{INTERFACE_ID}/",
                payload={
                    "id": INTERFACE_ID,
                    **NB_IFACE_NAMES,
                    "custom_fields": {IB_GUID_CF_KEY: GUID_A},
                },
            )

            result = await sync_ib_guid_on_interface(
                SyncIBGuidInput(interface_id=INTERFACE_ID, guid=GUID_A, dry_run=False)
            )

            patch_calls = [(k, v) for k, v in m.requests.items() if k[0].lower() == "patch"]
            assert patch_calls == []

        assert result.changed is False
        assert result.dry_run is False
        assert result.previous_guid == GUID_A
        assert result.new_guid == GUID_A
        assert result.device_name == "dgx-03"
        assert result.interface_name == "HCA-5/1"

    @pytest.mark.asyncio
    async def test_patches_when_changed(self, mock_nb_config):
        with aioresponses() as m:
            m.get(
                f"{NB_API}/dcim/interfaces/{INTERFACE_ID}/",
                payload={
                    "id": INTERFACE_ID,
                    **NB_IFACE_NAMES,
                    "custom_fields": {IB_GUID_CF_KEY: "0xOLD"},
                },
            )
            m.patch(
                f"{NB_API}/dcim/interfaces/{INTERFACE_ID}/",
                payload={"id": INTERFACE_ID},
            )

            result = await sync_ib_guid_on_interface(
                SyncIBGuidInput(interface_id=INTERFACE_ID, guid=GUID_A, dry_run=False)
            )

            patch_calls = [(k, v) for k, v in m.requests.items() if k[0].lower() == "patch"]
            assert len(patch_calls) == 1
            # aioresponses stores kwargs; inspect the body we sent.
            patch_kwargs = patch_calls[0][1][0].kwargs
            assert patch_kwargs["json"] == {"custom_fields": {IB_GUID_CF_KEY: GUID_A}}

        assert result.changed is True
        assert result.dry_run is False
        assert result.previous_guid == "0xOLD"
        assert result.new_guid == GUID_A
        assert result.device_name == "dgx-03"
        assert result.interface_name == "HCA-5/1"
