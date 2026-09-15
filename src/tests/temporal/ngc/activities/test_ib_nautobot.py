# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for the IB context resolver activity."""

from __future__ import annotations

from configparser import ConfigParser
from typing import Any
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_nautobot import (
    ResolveIBContextInput,
    ResolveIBSiteForHostInput,
    _normalize_pkey,
    _select_pkey_match,
    canonicalize_ufm_host,
    canonicalize_ufm_host_for_site,
    resolve_ib_context,
    resolve_ib_context_for_add,
    resolve_ib_site_for_host,
)

NB_URL = "https://nautobot.example.com"
NB_GRAPHQL = f"{NB_URL}/api/graphql/"

DEVICE_NAME = "ufm-01"
DEVICE_IP = "10.0.0.1"
DEVICE_ID = "dev-1234"
LOCATION_ID = "loc-7890"
LOCATION_NAME = "test-site"
DATAHALL_ID = "loc-datahall-1"
DATAHALL_NAME = "example-site-datahall-1"
SITE_ID = "loc-site-example"
SITE_NAME = "example-site"
OVERLAY_ID = "ovl-aaaa"
OVERLAY_NAME = "ib-pkey-0x0100"
PKEY_ID = "pky-bbbb"


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
def reset_secrets_cache() -> Any:
    clear_secrets_cache()
    yield
    clear_secrets_cache()


@pytest.fixture()
def mock_nb_config() -> Any:
    with patch(
        "nv_config_manager.common.config.load_config",
        return_value=_nb_config(),
    ):
        yield


def _overlay_block(
    *,
    overlay_id: str = OVERLAY_ID,
    overlay_name: str = OVERLAY_NAME,
    pkeys: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "id": overlay_id,
            "name": overlay_name,
            "pkeys": pkeys if pkeys is not None else [{"id": PKEY_ID, "pkey": "0x0100"}],
        }
    ]


def _device_payload(
    *,
    pkeys: list[dict[str, str]] | None = None,
    overlay_id: str = OVERLAY_ID,
    overlay_name: str = OVERLAY_NAME,
    device_id: str = DEVICE_ID,
) -> dict[str, Any]:
    return {
        "id": device_id,
        "name": DEVICE_NAME,
        "role": {"name": "UFM"},
        "primary_ip4": {"host": DEVICE_IP},
        "location": {
            "id": LOCATION_ID,
            "name": LOCATION_NAME,
            "location_type": {"name": "Site"},
            "overlays": _overlay_block(
                overlay_id=overlay_id, overlay_name=overlay_name, pkeys=pkeys
            ),
        },
    }


def _datahall_device_payload(
    *,
    overlay_at: str = "site",
    overlay_id: str = OVERLAY_ID,
    overlay_name: str = OVERLAY_NAME,
    pkeys: list[dict[str, str]] | None = None,
    extra_site_overlays: list[dict[str, Any]] | None = None,
    extra_datahall_overlays: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    site_overlays = list(extra_site_overlays or [])
    datahall_overlays = list(extra_datahall_overlays or [])

    overlay = _overlay_block(overlay_id=overlay_id, overlay_name=overlay_name, pkeys=pkeys)
    if overlay_at in {"site", "both"}:
        site_overlays = [*overlay, *site_overlays]
    if overlay_at in {"datahall", "both"}:
        datahall_overlays = [*overlay, *datahall_overlays]

    return {
        "id": DEVICE_ID,
        "name": DEVICE_NAME,
        "role": {"name": "UFM"},
        "primary_ip4": {"host": DEVICE_IP},
        "location": {
            "id": DATAHALL_ID,
            "name": DATAHALL_NAME,
            "location_type": {"name": "Datahall"},
            "overlays": datahall_overlays,
            "parent": {
                "id": SITE_ID,
                "name": SITE_NAME,
                "location_type": {"name": "Site"},
                "overlays": site_overlays,
                "parent": None,
            },
        },
    }


# ---------------------------------------------------------------------------
# _normalize_pkey -- pure helper
# ---------------------------------------------------------------------------


class TestNormalizePkey:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("0x100", "0x0100"),
            ("0x0100", "0x0100"),
            ("0X0100", "0x0100"),
            ("0xFFF", "0x0fff"),
            ("0x8001", "0x8001"),
            ("0x1", "0x0001"),
            ("0xfffe", "0xfffe"),
        ],
    )
    def test_canonicalizes_valid_forms(self, raw: str, expected: str) -> None:
        assert _normalize_pkey(raw) == expected

    @pytest.mark.parametrize(
        "bad",
        ["", "100", "0x", "0x12345", "0xZZZZ", "not-hex", "0x-1"],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ApplicationError, match="does not match required format"):
            _normalize_pkey(bad)


# ---------------------------------------------------------------------------
# _select_pkey_match -- pure helper
# ---------------------------------------------------------------------------


class TestSelectPkeyMatch:
    def test_finds_single_match(self) -> None:
        device = _device_payload()
        location, overlay, pkey_record = _select_pkey_match(device, "0x0100")
        assert location["id"] == LOCATION_ID
        assert overlay["id"] == OVERLAY_ID
        assert pkey_record["id"] == PKEY_ID

    def test_matches_across_pkey_format_variants(self) -> None:
        device = _device_payload(pkeys=[{"id": "p1", "pkey": "0x100"}])
        _, _, pkey_record = _select_pkey_match(device, "0x0100")
        assert pkey_record["id"] == "p1"

    def test_no_match_raises(self) -> None:
        device = _device_payload(pkeys=[{"id": "p1", "pkey": "0x8001"}])
        with pytest.raises(ApplicationError, match="not found at or above location"):
            _select_pkey_match(device, "0x0100")

    def test_ambiguous_match_at_same_location_raises(self) -> None:
        device = {
            "location": {
                "id": LOCATION_ID,
                "name": LOCATION_NAME,
                "overlays": [
                    {
                        "id": "ovl-a",
                        "name": "A",
                        "pkeys": [{"id": "p1", "pkey": "0x0100"}],
                    },
                    {
                        "id": "ovl-b",
                        "name": "B",
                        "pkeys": [{"id": "p2", "pkey": "0x100"}],
                    },
                ],
            },
        }
        with pytest.raises(ApplicationError, match="ambiguous near location"):
            _select_pkey_match(device, "0x0100")

    def test_ignores_malformed_pkey_in_data(self) -> None:
        device = _device_payload(
            pkeys=[{"id": "p1", "pkey": "junk"}, {"id": "p2", "pkey": "0x0100"}]
        )
        _, _, pkey_record = _select_pkey_match(device, "0x0100")
        assert pkey_record["id"] == "p2"

    def test_empty_overlays_raises_not_found(self) -> None:
        device = {"location": {"id": LOCATION_ID, "name": LOCATION_NAME, "overlays": []}}
        with pytest.raises(ApplicationError, match="not found at or above location"):
            _select_pkey_match(device, "0x0100")

    def test_finds_overlay_attached_at_site_parent(self) -> None:
        device = _datahall_device_payload(overlay_at="site")
        location, overlay, pkey_record = _select_pkey_match(device, "0x0100")
        assert location["id"] == SITE_ID
        assert location["name"] == SITE_NAME
        assert overlay["id"] == OVERLAY_ID
        assert pkey_record["id"] == PKEY_ID

    def test_same_overlay_at_two_levels_raises_ambiguous(self) -> None:
        device = _datahall_device_payload(overlay_at="both")
        with pytest.raises(ApplicationError, match="ambiguous near location"):
            _select_pkey_match(device, "0x0100")

    def test_no_overlay_in_chain_reports_device_location(self) -> None:
        device = _datahall_device_payload(overlay_at="neither")
        with pytest.raises(ApplicationError) as exc_info:
            _select_pkey_match(device, "0x0100")
        assert DATAHALL_NAME in str(exc_info.value)

    def test_walks_through_intermediate_location_without_overlays(self) -> None:
        device = _datahall_device_payload(overlay_at="site")
        device["location"]["parent"]["parent"] = {
            "id": "loc-region",
            "name": "us-east",
            "overlays": [],
            "parent": None,
        }
        location, _, _ = _select_pkey_match(device, "0x0100")
        assert location["id"] == SITE_ID


# ---------------------------------------------------------------------------
# resolve_ib_context activity -- name path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResolveIBContextByName:
    async def test_happy_path(self, mock_nb_config: Any) -> None:
        payload = {"data": {"devices": [_device_payload()]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_context(ResolveIBContextInput(host=DEVICE_NAME, pkey="0x100"))
        assert result.ufm_device_id == DEVICE_ID
        assert result.ufm_device_name == DEVICE_NAME
        assert result.location_id == LOCATION_ID
        assert result.location_name == LOCATION_NAME
        assert result.overlay_id == OVERLAY_ID
        assert result.overlay_name == OVERLAY_NAME
        assert result.pkey_id == PKEY_ID
        assert result.pkey == "0x0100"
        assert result.ufm_device_primary_ip == DEVICE_IP

    async def test_device_not_found(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": []}})
            with pytest.raises(ApplicationError, match="not found in Nautobot"):
                await resolve_ib_context(ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100"))

    async def test_multiple_devices_match(self, mock_nb_config: Any) -> None:
        payload = {
            "data": {
                "devices": [
                    _device_payload(),
                    _device_payload(device_id="dev-other"),
                ]
            }
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            with pytest.raises(ApplicationError, match="Multiple UFM devices"):
                await resolve_ib_context(ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100"))

    async def test_invalid_pkey_raises_before_query(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": []}})
            with pytest.raises(ApplicationError, match="does not match required format"):
                await resolve_ib_context(ResolveIBContextInput(host=DEVICE_NAME, pkey="bogus"))

    async def test_returns_site_when_overlay_lives_on_parent(self, mock_nb_config: Any) -> None:
        payload = {"data": {"devices": [_datahall_device_payload(overlay_at="site")]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_context(
                ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
            )
        assert result.location_id == SITE_ID
        assert result.location_name == SITE_NAME
        assert result.overlay_id == OVERLAY_ID

    async def test_returns_site_even_when_overlay_lives_at_datahall(
        self, mock_nb_config: Any
    ) -> None:
        payload = {"data": {"devices": [_datahall_device_payload(overlay_at="datahall")]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_context(
                ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
            )
        assert result.location_id == SITE_ID
        assert result.location_name == SITE_NAME
        assert result.overlay_id == OVERLAY_ID

    async def test_no_site_in_hierarchy_raises(self, mock_nb_config: Any) -> None:
        device = _datahall_device_payload(overlay_at="datahall")
        device["location"]["parent"]["location_type"] = {"name": "Region"}
        payload = {"data": {"devices": [device]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            with pytest.raises(ApplicationError, match="No Site-typed location"):
                await resolve_ib_context(ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100"))


# ---------------------------------------------------------------------------
# resolve_ib_context activity -- IP path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResolveIBContextByIP:
    async def test_happy_path(self, mock_nb_config: Any) -> None:
        payload = {
            "data": {
                "ip_addresses": [
                    {
                        "address": "10.0.0.1/32",
                        "interfaces": [{"device": _device_payload()}],
                    }
                ]
            }
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_context(ResolveIBContextInput(host=DEVICE_IP, pkey="0x0100"))
        assert result.ufm_device_id == DEVICE_ID
        assert result.pkey == "0x0100"

    async def test_ip_not_assigned(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"ip_addresses": []}})
            with pytest.raises(ApplicationError, match="not found in Nautobot"):
                await resolve_ib_context(ResolveIBContextInput(host=DEVICE_IP, pkey="0x0100"))

    async def test_dedupes_same_device_on_multiple_interfaces(self, mock_nb_config: Any) -> None:
        # IP path can list multiple interfaces, all belonging to the same device.
        payload = {
            "data": {
                "ip_addresses": [
                    {
                        "address": "10.0.0.1/32",
                        "interfaces": [
                            {"device": _device_payload()},
                            {"device": _device_payload()},
                        ],
                    }
                ]
            }
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_context(ResolveIBContextInput(host=DEVICE_IP, pkey="0x0100"))
        assert result.ufm_device_id == DEVICE_ID

    async def test_distinct_devices_share_ip_raises(self, mock_nb_config: Any) -> None:
        payload = {
            "data": {
                "ip_addresses": [
                    {
                        "address": "10.0.0.1/32",
                        "interfaces": [
                            {"device": _device_payload()},
                            {"device": _device_payload(device_id="dev-other")},
                        ],
                    }
                ]
            }
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            with pytest.raises(ApplicationError, match="Multiple UFM devices"):
                await resolve_ib_context(ResolveIBContextInput(host=DEVICE_IP, pkey="0x0100"))


# ---------------------------------------------------------------------------
# resolve_ib_site_for_host activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResolveIBSiteForHost:
    async def test_happy_path_by_name(self, mock_nb_config: Any) -> None:
        payload = {"data": {"devices": [_device_payload()]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=DEVICE_NAME))
        assert result.ufm_device_id == DEVICE_ID
        assert result.ufm_device_name == DEVICE_NAME
        assert result.ufm_device_primary_ip == DEVICE_IP
        assert result.location_id == LOCATION_ID
        assert result.location_name == LOCATION_NAME

    async def test_happy_path_by_ip(self, mock_nb_config: Any) -> None:
        payload = {
            "data": {
                "ip_addresses": [
                    {
                        "address": "10.0.0.1/32",
                        "interfaces": [{"device": _device_payload()}],
                    }
                ]
            }
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=DEVICE_IP))
        assert result.ufm_device_id == DEVICE_ID
        assert result.location_name == LOCATION_NAME

    async def test_walks_chain_to_site_when_device_in_datahall(self, mock_nb_config: Any) -> None:
        payload = {"data": {"devices": [_datahall_device_payload(overlay_at="datahall")]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=DEVICE_NAME))
        assert result.location_id == SITE_ID
        assert result.location_name == SITE_NAME

    async def test_succeeds_when_pkey_is_absent_from_nautobot(self, mock_nb_config: Any) -> None:
        """The lightweight resolver must not depend on PKey/overlay state."""
        device = _device_payload(pkeys=[])
        device["location"]["overlays"] = []
        payload = {"data": {"devices": [device]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=DEVICE_NAME))
        assert result.location_id == LOCATION_ID

    async def test_device_not_found_raises(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": []}})
            with pytest.raises(ApplicationError, match="not found in Nautobot"):
                await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=DEVICE_NAME))

    async def test_no_site_in_hierarchy_raises(self, mock_nb_config: Any) -> None:
        device = _datahall_device_payload(overlay_at="datahall")
        device["location"]["parent"]["location_type"] = {"name": "Region"}
        payload = {"data": {"devices": [device]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            with pytest.raises(ApplicationError, match="No Site-typed location"):
                await resolve_ib_site_for_host(ResolveIBSiteForHostInput(host=DEVICE_NAME))


# ---------------------------------------------------------------------------
# resolve_ib_context_for_add activity -- lazy-create-overlay path
# ---------------------------------------------------------------------------


TENANT_ID = "tenant-uuid-1"
TENANT_NAME = "test-tenant"
ORPHAN_PKEY_ID = "orphan-pkey-1"
NEW_OVERLAY_ID = "ovl-new-1"
NEW_OVERLAY_NAME = "ib-pkey-overlay-0x0100"
STATUS_ID = "status-active-uuid"

NB_PKEYS = f"{NB_URL}/api/plugins/overlays/pkeys/"
NB_OVERLAYS = f"{NB_URL}/api/plugins/overlays/overlays/"
NB_STATUSES = f"{NB_URL}/api/extras/statuses/"


def _device_payload_with_tenant(
    *,
    pkeys: list[dict[str, str]] | None = None,
    tenant_id: str | None = TENANT_ID,
) -> dict[str, Any]:
    device = _device_payload(pkeys=pkeys)
    if tenant_id is None:
        device["tenant"] = None
    else:
        device["tenant"] = {"id": tenant_id, "name": TENANT_NAME}
    return device


def _device_payload_no_overlays(
    *,
    tenant_id: str | None = TENANT_ID,
) -> dict[str, Any]:
    device = _device_payload_with_tenant(tenant_id=tenant_id)
    device["location"]["overlays"] = []
    return device


def _datahall_device_payload_with_tenant_no_overlays(
    *,
    tenant_id: str | None = TENANT_ID,
) -> dict[str, Any]:
    """Device installed at a Datahall under a Site, with empty overlays."""
    device = _datahall_device_payload(overlay_at="site")
    device["location"]["overlays"] = []
    device["location"]["parent"]["overlays"] = []
    if tenant_id is None:
        device["tenant"] = None
    else:
        device["tenant"] = {"id": tenant_id, "name": TENANT_NAME}
    return device


@pytest.mark.asyncio
class TestResolveIBContextForAdd:
    async def test_existing_overlay_no_lazy_create(self, mock_nb_config: Any) -> None:
        """When an Overlay-bound PKey is found at the site, do not touch REST."""
        payload = {"data": {"devices": [_device_payload_with_tenant()]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            result = await resolve_ib_context_for_add(
                ResolveIBContextInput(host=DEVICE_NAME, pkey="0x100")
            )
        assert result.overlay_id == OVERLAY_ID
        assert result.overlay_name == OVERLAY_NAME
        assert result.pkey_id == PKEY_ID
        assert result.pkey == "0x0100"

    async def test_orphan_pkey_lazy_creates_overlay(self, mock_nb_config: Any) -> None:
        """Orphan PKey + missing Overlay -> create Overlay and link PKey to it."""
        gql_payload = {"data": {"devices": [_device_payload_no_overlays()]}}

        orphan = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": None}
        new_overlay = {"id": NEW_OVERLAY_ID, "name": NEW_OVERLAY_NAME}
        linked_pkey = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": NEW_OVERLAY_ID}

        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(
                f"{NB_PKEYS}?pkey=0x0100",
                payload={"results": [orphan]},
            )
            m.get(
                f"{NB_OVERLAYS}?name={NEW_OVERLAY_NAME}&location={LOCATION_ID}",
                payload={"results": []},
            )
            m.get(
                f"{NB_STATUSES}?name=Active",
                payload={"results": [{"id": STATUS_ID, "name": "Active"}]},
            )
            m.post(NB_OVERLAYS, payload=new_overlay)
            m.get(f"{NB_PKEYS}{ORPHAN_PKEY_ID}/", payload=orphan)
            m.patch(f"{NB_PKEYS}{ORPHAN_PKEY_ID}/", payload=linked_pkey)

            result = await resolve_ib_context_for_add(
                ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
            )

        assert result.overlay_id == NEW_OVERLAY_ID
        assert result.overlay_name == NEW_OVERLAY_NAME
        assert result.pkey_id == ORPHAN_PKEY_ID
        assert result.location_id == LOCATION_ID

    async def test_idempotent_when_overlay_already_exists(self, mock_nb_config: Any) -> None:
        """On retry: an Overlay with the same (name, location) is reused (no POST)."""
        gql_payload = {"data": {"devices": [_device_payload_no_overlays()]}}

        orphan = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": None}
        existing_overlay = {"id": NEW_OVERLAY_ID, "name": NEW_OVERLAY_NAME}
        already_linked = {
            "id": ORPHAN_PKEY_ID,
            "pkey": "0x0100",
            "overlay": NEW_OVERLAY_ID,
        }

        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(f"{NB_PKEYS}?pkey=0x0100", payload={"results": [orphan]})
            m.get(
                f"{NB_OVERLAYS}?name={NEW_OVERLAY_NAME}&location={LOCATION_ID}",
                payload={"results": [existing_overlay]},
            )
            m.get(f"{NB_PKEYS}{ORPHAN_PKEY_ID}/", payload=already_linked)

            result = await resolve_ib_context_for_add(
                ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
            )

        assert result.overlay_id == NEW_OVERLAY_ID
        assert result.pkey_id == ORPHAN_PKEY_ID

    async def test_pkey_linked_to_different_overlay_raises(self, mock_nb_config: Any) -> None:
        """If the orphan PKey is already linked to a *different* overlay, refuse to relink."""
        gql_payload = {"data": {"devices": [_device_payload_no_overlays()]}}

        orphan = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": None}
        existing_overlay = {"id": NEW_OVERLAY_ID, "name": NEW_OVERLAY_NAME}
        already_linked_elsewhere = {
            "id": ORPHAN_PKEY_ID,
            "pkey": "0x0100",
            "overlay": "some-other-overlay-id",
        }

        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(f"{NB_PKEYS}?pkey=0x0100", payload={"results": [orphan]})
            m.get(
                f"{NB_OVERLAYS}?name={NEW_OVERLAY_NAME}&location={LOCATION_ID}",
                payload={"results": [existing_overlay]},
            )
            m.get(f"{NB_PKEYS}{ORPHAN_PKEY_ID}/", payload=already_linked_elsewhere)

            with pytest.raises(ApplicationError, match="already linked to Overlay"):
                await resolve_ib_context_for_add(
                    ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
                )

    async def test_no_overlay_no_orphan_raises_with_creation_hint(
        self, mock_nb_config: Any
    ) -> None:
        """Missing Overlay AND missing PKey row -> hard fail with hint to creation workflow."""
        gql_payload = {"data": {"devices": [_device_payload_no_overlays()]}}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(f"{NB_PKEYS}?pkey=0x0100", payload={"results": []})

            with pytest.raises(ApplicationError, match="Run the IB PKey Creation workflow"):
                await resolve_ib_context_for_add(
                    ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
                )

    async def test_multiple_orphan_pkeys_raise(self, mock_nb_config: Any) -> None:
        """Multiple unlinked PKey rows are ambiguous and must be cleaned up."""
        gql_payload = {"data": {"devices": [_device_payload_no_overlays()]}}
        orphan_a = {
            "id": "orphan-pkey-a",
            "name": "PKey-0x0100-a",
            "pkey": "0x0100",
            "overlay": None,
        }
        orphan_b = {
            "id": "orphan-pkey-b",
            "name": "PKey-0x0100-b",
            "pkey": "0x0100",
            "overlay": None,
        }

        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(f"{NB_PKEYS}?pkey=0x0100", payload={"results": [orphan_a, orphan_b]})

            with pytest.raises(ApplicationError, match="Multiple orphan InfiniBandPKey rows"):
                await resolve_ib_context_for_add(
                    ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
                )

    async def test_lazy_creates_at_device_location_not_site(self, mock_nb_config: Any) -> None:
        """When the device lives below the Site (e.g. Datahall), the new Overlay
        is placed at the device's immediate location, NOT promoted up to the Site.
        The returned ``location_id`` still refers to the Site (used for credentials).
        """
        gql_payload = {"data": {"devices": [_datahall_device_payload_with_tenant_no_overlays()]}}

        orphan = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": None}
        new_overlay = {"id": NEW_OVERLAY_ID, "name": NEW_OVERLAY_NAME}
        linked_pkey = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": NEW_OVERLAY_ID}

        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(f"{NB_PKEYS}?pkey=0x0100", payload={"results": [orphan]})
            m.get(
                f"{NB_OVERLAYS}?name={NEW_OVERLAY_NAME}&location={DATAHALL_ID}",
                payload={"results": []},
            )
            m.get(
                f"{NB_STATUSES}?name=Active",
                payload={"results": [{"id": STATUS_ID, "name": "Active"}]},
            )
            m.post(NB_OVERLAYS, payload=new_overlay)
            m.get(f"{NB_PKEYS}{ORPHAN_PKEY_ID}/", payload=orphan)
            m.patch(f"{NB_PKEYS}{ORPHAN_PKEY_ID}/", payload=linked_pkey)

            result = await resolve_ib_context_for_add(
                ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
            )

        assert result.overlay_id == NEW_OVERLAY_ID
        assert result.location_id == SITE_ID
        assert result.location_name == SITE_NAME

    async def test_orphan_without_device_tenant_raises(self, mock_nb_config: Any) -> None:
        """Cannot lazy-create Overlay when the device has no Tenant set."""
        gql_payload = {"data": {"devices": [_device_payload_no_overlays(tenant_id=None)]}}
        orphan = {"id": ORPHAN_PKEY_ID, "pkey": "0x0100", "overlay": None}

        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=gql_payload)
            m.get(f"{NB_PKEYS}?pkey=0x0100", payload={"results": [orphan]})

            with pytest.raises(ApplicationError, match="has no Tenant set"):
                await resolve_ib_context_for_add(
                    ResolveIBContextInput(host=DEVICE_NAME, pkey="0x0100")
                )


# ---------------------------------------------------------------------------
# canonicalize_ufm_host -- API-boundary host canonicalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCanonicalizeUFMHost:
    """Name or IP collapses to one canonical identifier for the lock key."""

    async def test_name_resolves_to_primary_ip(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": [_device_payload()]}})
            assert await canonicalize_ufm_host(DEVICE_NAME) == DEVICE_IP

    async def test_ip_resolves_to_primary_ip(self, mock_nb_config: Any) -> None:
        payload = {
            "data": {
                "ip_addresses": [
                    {"address": "10.0.0.1/32", "interfaces": [{"device": _device_payload()}]}
                ]
            }
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload=payload)
            assert await canonicalize_ufm_host(DEVICE_IP) == DEVICE_IP

    async def test_falls_back_to_name_without_primary_ip(self, mock_nb_config: Any) -> None:
        device = {
            "id": DEVICE_ID,
            "name": DEVICE_NAME,
            "role": {"name": "UFM"},
            "primary_ip4": None,
            "location": {},
        }
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": [device]}})
            assert await canonicalize_ufm_host(DEVICE_NAME) == DEVICE_NAME

    async def test_device_not_found_raises(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": []}})
            with pytest.raises(ApplicationError, match="not found in Nautobot"):
                await canonicalize_ufm_host(DEVICE_NAME)


@pytest.mark.asyncio
class TestCanonicalizeUFMHostForSite:
    """API UFM targets must exist and belong to the supplied credential Site."""

    @pytest.mark.parametrize("site_reference", [LOCATION_ID, LOCATION_NAME, None])
    async def test_accepts_matching_site_or_no_override(
        self,
        mock_nb_config: Any,
        site_reference: str | None,
    ) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": [_device_payload()]}})
            result = await canonicalize_ufm_host_for_site(DEVICE_NAME, site_reference)

        assert result == DEVICE_IP

    async def test_rejects_site_from_another_ufm(self, mock_nb_config: Any) -> None:
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": [_device_payload()]}})
            with pytest.raises(ApplicationError, match="belongs to Site.*not 'other-site'"):
                await canonicalize_ufm_host_for_site(DEVICE_NAME, "other-site")

    async def test_rejects_non_ufm_device(self, mock_nb_config: Any) -> None:
        device = _device_payload()
        device["role"] = {"name": "Leaf"}
        with aioresponses() as m:
            m.post(NB_GRAPHQL, payload={"data": {"devices": [device]}})
            with pytest.raises(ApplicationError, match="not assigned the UFM role"):
                await canonicalize_ufm_host_for_site(DEVICE_NAME, LOCATION_NAME)
