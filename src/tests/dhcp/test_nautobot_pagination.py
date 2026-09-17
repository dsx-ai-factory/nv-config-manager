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
"""Tests for DHCP Nautobot GraphQL pagination."""

from typing import Any

import pytest
from nv_config_manager_dcim_nautobot_2x.dhcp import DHCPDataError
from nv_config_manager_dcim_nautobot_2x.provider import NautobotDCIMClient as NautobotClient


class _PagingNautobotClient(NautobotClient):
    """Return sliced GraphQL lists and record (limit, offset) per result key."""

    def __init__(self, pages: dict[str, list[Any]]) -> None:
        super().__init__("https://nautobot.example.com/", "dummy")
        self._pages = pages
        self.calls: list[tuple[str, int, int]] = []

    async def graphql_query(self, query, variables=None):  # noqa: ANN001
        variables = variables or {}
        limit = variables["limit"]
        offset = variables["offset"]
        if "config_manager_devices" in query:
            key = "config_manager_devices"
        elif "dhcp-pool" in query:
            key = "pool_ips"
        elif "dhcp-reserve" in query:
            key = "reserved_ips"
        elif "dhcp-subnet" in query:
            key = "prefixes"
        else:
            raise AssertionError(f"unexpected query: {query}")
        self.calls.append((key, limit, offset))
        items = self._pages.get(key, [])
        return {"data": {key: items[offset : offset + limit]}}


_PREFIX_QUERY = 'query { prefixes(tags: ["dhcp-subnet"]) { id } }'
_RESERVED_QUERY = 'query { ip_addresses(tags: ["dhcp-reserve"]) { id } }'


def _twice(calls: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Each GraphQL list is walked twice; walks of one list are consecutive."""
    return calls + calls


class _DeletingNautobotClient(_PagingNautobotClient):
    """Drop the first row of a result key once its first page has been read."""

    def __init__(self, pages: dict[str, list[Any]], delete_from: str) -> None:
        super().__init__(pages)
        self._delete_from = delete_from
        self._deleted = False

    async def graphql_query(self, query, variables=None):  # noqa: ANN001
        response = await super().graphql_query(query, variables)
        if not self._deleted and self.calls[-1][0] == self._delete_from:
            self._pages[self._delete_from] = self._pages[self._delete_from][1:]
            self._deleted = True
        return response


class _DeleteAfterFirstWalk(_PagingNautobotClient):
    """Remove a row after the first walk of a key ends (short or empty page)."""

    def __init__(self, pages: dict[str, list[Any]]) -> None:
        super().__init__(pages)
        self._deleted: set[str] = set()

    async def graphql_query(self, query, variables=None):  # noqa: ANN001
        response = await super().graphql_query(query, variables)
        key, limit, _offset = self.calls[-1]
        page = response["data"][key]
        if key not in self._deleted and (not page or len(page) < limit):
            self._pages[key] = self._pages[key][1:]
            self._deleted.add(key)
        return response


def _device_page(n: int) -> list[dict[str, Any]]:
    return [{"device": {"id": f"dev-{i}", "config_context": {"n": i}}} for i in range(n)]


def _prefix_page(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"prefix-{i}",
            "prefix": f"10.0.{i}.0/24",
            "ip_version": 4,
            "rel_prefix_to_gateway": {"address": f"10.0.{i}.1/24"},
        }
        for i in range(n)
    ]


def _reserved_ip(record_id: str, parent_id: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "address": "10.0.0.50/24",
        "ip_version": 4,
        "parent": {"id": parent_id},
        "interfaces": [
            {
                "name": "eth0",
                "mac_address": "00:11:22:33:44:55",
                "role": {"name": "management"},
                "device": {
                    "id": f"dev-{record_id}",
                    "name": f"leaf-{record_id}",
                    "serial": f"SN-{record_id}",
                    "platform": {"name": "Cumulus Linux"},
                    "status": {"name": "active"},
                    "configmanagerdevicestatus": {
                        "ztp_enabled": True,
                        "is_aggregate_managed": False,
                    },
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_load_dhcp_contexts_follows_limit_offset_pages() -> None:
    client = _PagingNautobotClient({"config_manager_devices": _device_page(5)})
    contexts = await client.load_dhcp_contexts(page_size=2, overlap=0)

    assert list(contexts) == [f"dev-{i}" for i in range(5)]
    assert contexts["dev-4"] == {"n": 4}
    assert client.calls == _twice(
        [
            ("config_manager_devices", 2, 0),
            ("config_manager_devices", 2, 2),
            ("config_manager_devices", 2, 4),
        ]
    )


@pytest.mark.asyncio
async def test_load_dhcp_contexts_empty_first_page() -> None:
    client = _PagingNautobotClient({"config_manager_devices": []})
    assert await client.load_dhcp_contexts(page_size=2) == {}
    assert client.calls == _twice([("config_manager_devices", 2, 0)])


@pytest.mark.asyncio
async def test_load_auto_dhcp_subnets_pages_each_list_independently() -> None:
    prefixes = _prefix_page(3)
    pool_ips = [
        {
            "address": f"10.0.0.{i}/24",
            "ip_version": 4,
            "parent": {"id": "prefix-0"},
            "interfaces": [],
        }
        for i in range(4)
    ]
    reserved_ips = [
        {
            "address": "10.0.0.50/24",
            "ip_version": 4,
            "parent": {"id": "prefix-0"},
            "interfaces": [
                {
                    "name": "eth0",
                    "mac_address": "00:11:22:33:44:55",
                    "role": {"name": "management"},
                    "device": {
                        "id": "dev-0",
                        "name": "leaf-0",
                        "serial": "SN0",
                        "platform": {"name": "Cumulus Linux"},
                        "status": {"name": "active"},
                        "configmanagerdevicestatus": {
                            "ztp_enabled": True,
                            "is_aggregate_managed": False,
                        },
                    },
                }
            ],
        }
    ]
    client = _PagingNautobotClient(
        {"prefixes": prefixes, "pool_ips": pool_ips, "reserved_ips": reserved_ips}
    )

    subnets = await client.load_auto_dhcp_subnets(
        family=4, is_aggregate_managed=False, page_size=2, overlap=0
    )

    assert len(subnets) == 3
    prefix_0 = next(s for s in subnets if str(s["id"]) == "prefix-0")
    assert len(prefix_0["pool_ips"]) == 4
    assert len(prefix_0["reservations"]) == 1
    # Four pool IPs fill two pages of size 2; the next request is empty and stops.
    # Each list is walked twice so a torn read cannot publish.
    assert client.calls == (
        _twice([("prefixes", 2, 0), ("prefixes", 2, 2)])
        + _twice([("pool_ips", 2, 0), ("pool_ips", 2, 2), ("pool_ips", 2, 4)])
        + _twice([("reserved_ips", 2, 0)])
    )


@pytest.mark.asyncio
async def test_load_auto_dhcp_subnets_stops_without_ip_queries_when_no_prefixes() -> None:
    client = _PagingNautobotClient({"prefixes": [], "pool_ips": [{"address": "1.1.1.1/32"}]})
    assert await client.load_auto_dhcp_subnets(page_size=2) == []
    assert client.calls == _twice([("prefixes", 2, 0)])


@pytest.mark.asyncio
async def test_iter_graphql_pages_raises_when_a_page_repeats() -> None:
    """A backend ignoring limit/offset serves the same page for every offset."""

    class _StuckClient(NautobotClient):
        def __init__(self) -> None:
            super().__init__("https://nautobot.example.com/", "dummy")
            self.requests = 0

        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            self.requests += 1
            return {"data": {"prefixes": [{"id": "same"}] * 2}}

    client = _StuckClient()
    with pytest.raises(DHCPDataError, match="ignoring limit/offset"):
        await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=2)
    assert client.requests == 2


@pytest.mark.asyncio
async def test_iter_graphql_pages_raises_when_a_page_only_reorders() -> None:
    """An unordered backend repeats the same rows in a new sequence."""

    class _ShufflingClient(NautobotClient):
        def __init__(self) -> None:
            super().__init__("https://nautobot.example.com/", "dummy")
            self.requests = 0

        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            self.requests += 1
            rows = [{"id": "prefix-0"}, {"id": "prefix-1"}]
            return {"data": {"prefixes": rows if self.requests % 2 else rows[::-1]}}

    client = _ShufflingClient()
    with pytest.raises(DHCPDataError, match="ignoring limit/offset"):
        await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=2)
    assert client.requests == 2


@pytest.mark.asyncio
async def test_iter_graphql_pages_raises_when_pages_cycle() -> None:
    """Alternating pages look like progress against only the previous page."""

    class _CyclingClient(NautobotClient):
        def __init__(self) -> None:
            super().__init__("https://nautobot.example.com/", "dummy")
            self.requests = 0

        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            self.requests += 1
            first = [{"id": "prefix-0"}, {"id": "prefix-1"}]
            second = [{"id": "prefix-2"}, {"id": "prefix-3"}]
            return {"data": {"prefixes": first if self.requests % 2 else second}}

    client = _CyclingClient()
    with pytest.raises(DHCPDataError, match="ignoring limit/offset"):
        await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=2)
    assert client.requests == 3


@pytest.mark.asyncio
async def test_iter_graphql_pages_detects_a_stall_in_rows_without_ids() -> None:
    """Rows that carry no id fall back to a whole-row signature."""

    class _NoIdClient(NautobotClient):
        def __init__(self) -> None:
            super().__init__("https://nautobot.example.com/", "dummy")
            self.requests = 0

        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            self.requests += 1
            return {"data": {"prefixes": [{"prefix": "10.0.0.0/24"}, {"prefix": "10.0.1.0/24"}]}}

    client = _NoIdClient()
    with pytest.raises(DHCPDataError, match="ignoring limit/offset"):
        await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=2)
    assert client.requests == 2


@pytest.mark.asyncio
async def test_iter_graphql_pages_allows_a_short_page_inside_the_overlap() -> None:
    """The last page can be entirely overlap once the rows run out exactly."""
    client = _PagingNautobotClient({"prefixes": _prefix_page(4)})

    rows = await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=4, overlap=2)

    assert client.calls == [("prefixes", 4, 0), ("prefixes", 4, 2)]
    assert {row["id"] for row in rows} == {f"prefix-{i}" for i in range(4)}


@pytest.mark.asyncio
async def test_iter_graphql_pages_rejects_non_positive_page_size() -> None:
    client = _PagingNautobotClient({"prefixes": [{"id": "prefix-0"}]})
    with pytest.raises(ValueError, match="page_size"):
        await client.load_auto_dhcp_subnets(page_size=0)
    assert client.calls == []


@pytest.mark.asyncio
async def test_load_dhcp_contexts_skips_entries_without_devices() -> None:
    client = _PagingNautobotClient(
        {
            "config_manager_devices": [
                {"device": None},
                {"device": {"id": "dev-1", "config_context": {"n": 1}}},
            ]
        }
    )
    contexts = await client.load_dhcp_contexts(page_size=2)
    assert contexts == {"dev-1": {"n": 1}}


@pytest.mark.asyncio
async def test_iter_graphql_pages_rejects_malformed_result() -> None:
    class _BadClient(NautobotClient):
        def __init__(self) -> None:
            super().__init__("https://nautobot.example.com/", "dummy")

        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            return {"data": {"prefixes": {"id": "nope"}}}

    client = _BadClient()
    with pytest.raises(DHCPDataError, match="invalid prefixes"):
        await client._iter_graphql_pages("query { prefixes }", "prefixes", page_size=2)


@pytest.mark.asyncio
async def test_iter_graphql_pages_rejects_null_page_entries() -> None:
    class _NullEntryClient(NautobotClient):
        def __init__(self) -> None:
            super().__init__("https://nautobot.example.com/", "dummy")

        async def graphql_query(self, query, variables=None):  # noqa: ANN001
            return {"data": {"prefixes": [None, {"id": "prefix-0"}]}}

    client = _NullEntryClient()
    with pytest.raises(DHCPDataError, match="invalid prefixes"):
        await client._iter_graphql_pages("query { prefixes }", "prefixes", page_size=2)


@pytest.mark.asyncio
async def test_iter_graphql_pages_overlaps_page_tails() -> None:
    client = _PagingNautobotClient({"prefixes": _prefix_page(5)})

    rows = await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=3, overlap=1)

    assert client.calls == [("prefixes", 3, 0), ("prefixes", 3, 2), ("prefixes", 3, 4)]
    assert [row["id"] for row in rows] == [
        "prefix-0",
        "prefix-1",
        "prefix-2",
        "prefix-2",
        "prefix-3",
        "prefix-4",
        "prefix-4",
    ]


@pytest.mark.asyncio
async def test_iter_graphql_pages_clamps_overlap_below_page_size() -> None:
    client = _PagingNautobotClient({"prefixes": _prefix_page(3)})

    rows = await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", page_size=2, overlap=99)

    assert client.calls == [("prefixes", 2, 0), ("prefixes", 2, 1), ("prefixes", 2, 2)]
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_iter_graphql_pages_rejects_negative_overlap() -> None:
    client = _PagingNautobotClient({"prefixes": _prefix_page(1)})

    with pytest.raises(ValueError, match="overlap"):
        await client._iter_graphql_pages(_PREFIX_QUERY, "prefixes", overlap=-1)
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("overlap", "expected_reservations"), [(1, 4), (0, 3)])
async def test_overlap_recovers_row_shifted_by_concurrent_delete(
    overlap: int, expected_reservations: int
) -> None:
    """A delete during paging shifts later rows left; the overlap re-reads them.

    With no overlap the shifted row falls between two pages and is lost, which
    is the failure this parameter exists to prevent.
    """
    client = _DeletingNautobotClient(
        {
            "prefixes": _prefix_page(1),
            "pool_ips": [],
            "reserved_ips": [_reserved_ip(f"ip-{i}", "prefix-0") for i in range(4)],
        },
        delete_from="reserved_ips",
    )

    rows = await client._iter_graphql_pages(
        _RESERVED_QUERY, "reserved_ips", page_size=2, overlap=overlap
    )

    assert len({row["id"] for row in rows}) == expected_reservations


@pytest.mark.asyncio
async def test_load_auto_dhcp_subnets_keeps_same_address_with_distinct_ids() -> None:
    prefixes = _prefix_page(2)
    client = _PagingNautobotClient(
        {
            "prefixes": prefixes,
            "pool_ips": [],
            "reserved_ips": [
                _reserved_ip("ip-a", "prefix-0"),
                _reserved_ip("ip-b", "prefix-1"),
            ],
        }
    )

    subnets = await client.load_auto_dhcp_subnets(family=4, is_aggregate_managed=False, page_size=2)

    by_id = {str(subnet["id"]): subnet for subnet in subnets}
    assert len(by_id["prefix-0"]["reservations"]) == 1
    assert len(by_id["prefix-1"]["reservations"]) == 1


@pytest.mark.asyncio
async def test_load_stable_pages_returns_first_walk_when_ids_match() -> None:
    client = _PagingNautobotClient({"prefixes": _prefix_page(3)})

    rows = await client._load_stable_pages(_PREFIX_QUERY, "prefixes", page_size=2, overlap=0)

    assert [row["id"] for row in rows] == ["prefix-0", "prefix-1", "prefix-2"]
    assert client.calls == _twice([("prefixes", 2, 0), ("prefixes", 2, 2)])


@pytest.mark.asyncio
async def test_load_stable_pages_raises_when_walks_disagree() -> None:
    client = _DeleteAfterFirstWalk({"prefixes": _prefix_page(3)})

    with pytest.raises(DHCPDataError, match="changed during paging"):
        await client._load_stable_pages(_PREFIX_QUERY, "prefixes", page_size=2, overlap=0)


@pytest.mark.asyncio
async def test_load_dhcp_contexts_raises_when_walks_disagree() -> None:
    client = _DeleteAfterFirstWalk({"config_manager_devices": _device_page(3)})

    with pytest.raises(DHCPDataError, match="changed during paging"):
        await client.load_dhcp_contexts(page_size=2, overlap=0)
