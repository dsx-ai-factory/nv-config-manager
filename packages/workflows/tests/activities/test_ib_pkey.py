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
"""Unit tests for reusable InfiniBand PKey activities."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_pkey import (
    AddGuidsInput,
    CreatePKeyInput,
    FetchPKeyMembersInput,
    RemoveGuidsInput,
    SetGuidsInput,
    ValidatePKeyInput,
    VerifyPKeyInput,
    VerifyPKeyMembersAbsentInput,
    VerifyPKeyMembersInput,
    add_guids_to_pkey,
    create_pkey_on_ufm,
    fetch_pkey_members,
    remove_guids_from_pkey,
    set_pkey_members,
    validate_pkey_available,
    verify_pkey_created,
    verify_pkey_members,
    verify_pkey_members_absent,
)
from nv_config_manager_workflows.activities.ib_pkey.helpers import (
    extract_pkey_strings,
    find_next_available_pkey,
    parse_pkey_int,
)
from nv_config_manager_workflows.clients.ufm import UFMClient, UFMClientError
from nv_config_manager_workflows.runtime import configure_ufm_client

HOST = "ufm.example.test"
PKEY = "0x0005"


def _ufm_client() -> MagicMock:
    client = MagicMock(spec=UFMClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request = AsyncMock()
    configure_ufm_client(lambda _host, _site: cast(UFMClient, client))
    return client


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0x8001", 1),
        ("0xFFFF", 0x7FFF),
        ("0x7fff", 0x7FFF),
        ("0x0001", 1),
    ],
)
def test_parse_pkey_int_strips_the_membership_bit(raw: str, expected: int) -> None:
    assert parse_pkey_int(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"0x0001": {}, "0x0002": {}}, ["0x0001", "0x0002"]),
        (["0x0001", {"pkey": "0x0002"}, {}], ["0x0001", "0x0002"]),
        (None, []),
    ],
)
def test_extract_pkey_strings_preserves_supported_ufm_shapes(
    raw: object, expected: list[str]
) -> None:
    assert extract_pkey_strings(raw) == expected


def test_find_next_available_pkey_returns_minimum_for_empty_set() -> None:
    assert find_next_available_pkey(set(), 1, 100) == 1


def test_find_next_available_pkey_skips_existing_values() -> None:
    assert find_next_available_pkey({1, 2, 3}, 1, 100) == 4


def test_find_next_available_pkey_skips_reserved_values() -> None:
    assert find_next_available_pkey(set(), 0x7FFE, 0x7FFF) == 0x7FFE


def test_find_next_available_pkey_reports_exhaustion() -> None:
    assert find_next_available_pkey(set(range(1, 11)), 1, 10) is None


def test_find_next_available_pkey_uses_the_first_gap() -> None:
    assert find_next_available_pkey({1, 2, 4}, 1, 5) == 3


async def test_validate_pkey_auto_assigns_lowest_available_value() -> None:
    client = _ufm_client()
    client.request.return_value = {"0x0001": {}, "0x8002": {}}

    output = await validate_pkey_available(ValidatePKeyInput(host=HOST, pkey_min=1, pkey_max=4))

    assert output.pkey == "0x0003"
    assert output.auto_assigned is True
    assert output.existing_pkeys == ["0x0001", "0x8002"]
    assert output.display == "PKey 0x0003 is available (auto-assigned)"
    client.request.assert_awaited_once_with("GET", "/resources/pkeys")


async def test_validate_requested_pkey_collision_is_non_retryable() -> None:
    client = _ufm_client()
    client.request.return_value = ["0x0001"]

    with pytest.raises(ApplicationError) as exc_info:
        await validate_pkey_available(ValidatePKeyInput(host=HOST, pkey="0x8001"))

    assert exc_info.value.message == f"PKey 0x8001 is already in use on UFM at {HOST}"
    assert exc_info.value.non_retryable is True


async def test_create_pkey_preserves_payload_and_omits_deprecated_index0() -> None:
    client = _ufm_client()

    output = await create_pkey_on_ufm(
        CreatePKeyInput(host=HOST, pkey=PKEY, ip_over_ib=False, index0=True)
    )

    assert output.created is True
    assert output.pkey == PKEY
    client.request.assert_awaited_once_with(
        "POST",
        "/resources/pkeys/add",
        json={"pkey": PKEY, "ip_over_ib": False},
    )


async def test_verify_pkey_created_preserves_response() -> None:
    client = _ufm_client()
    response = {"pkey": PKEY, "ip_over_ib": True}
    client.request.return_value = response

    output = await verify_pkey_created(VerifyPKeyInput(host=HOST, pkey=PKEY))

    assert output.verified is True
    assert output.pkey_data == response
    client.request.assert_awaited_once_with("GET", f"/resources/pkeys/{PKEY}")


async def test_add_guids_merges_case_insensitively_and_preserves_current_ip_setting() -> None:
    client = _ufm_client()
    client.request.side_effect = [
        {
            "guids": [
                {"guid": "GUID-1", "membership": "FULL"},
                {"guid": "guid-2", "membership": "limited"},
            ],
            "ip_over_ib": False,
        },
        None,
    ]

    output = await add_guids_to_pkey(
        AddGuidsInput(
            host=HOST,
            pkey=PKEY,
            guids=["GUID-2", "GUID-3"],
            memberships=["FULL", "LIMITED"],
            ip_over_ib=True,
        )
    )

    assert output.guids_added == ["GUID-2", "GUID-3"]
    assert client.request.await_args_list == [
        call("GET", f"/resources/pkeys/{PKEY}", params={"guids_data": "true"}),
        call(
            "PUT",
            "/resources/pkeys/",
            json={
                "pkey": PKEY,
                "guids": ["guid-1", "guid-2", "guid-3"],
                "memberships": ["full", "full", "limited"],
                "ip_over_ib": False,
            },
        ),
    ]


async def test_add_guids_rejects_misaligned_memberships_without_opening_client() -> None:
    client = _ufm_client()

    with pytest.raises(ApplicationError) as exc_info:
        await add_guids_to_pkey(
            AddGuidsInput(host=HOST, pkey=PKEY, guids=["guid-1"], memberships=[])
        )

    assert exc_info.value.non_retryable is True
    client.__aenter__.assert_not_awaited()


async def test_fetch_members_preserves_order_memberships_and_ip_setting() -> None:
    client = _ufm_client()
    client.request.return_value = {
        "guids": [
            {"guid": "GUID-2", "membership": "LIMITED"},
            {"guid": "guid-1", "membership": "full"},
        ],
        "ip_over_ib": False,
    }

    output = await fetch_pkey_members(FetchPKeyMembersInput(host=HOST, pkey=PKEY))

    assert output.exists is True
    assert output.guids == ["guid-2", "guid-1"]
    assert output.memberships == ["limited", "full"]
    assert output.ip_over_ib is False


async def test_fetch_members_translates_404_to_missing_partition() -> None:
    client = _ufm_client()
    client.request.side_effect = UFMClientError("not found", status_code=404)

    output = await fetch_pkey_members(FetchPKeyMembersInput(host=HOST, pkey=PKEY))

    assert output.exists is False
    assert output.guids == []
    assert output.memberships == []
    assert output.ip_over_ib is None


async def test_remove_guids_preserves_csv_path_and_input_order() -> None:
    client = _ufm_client()

    output = await remove_guids_from_pkey(
        RemoveGuidsInput(host=HOST, pkey=PKEY, guids=["guid-2", "guid-1"])
    )

    assert output.guids_removed == ["guid-2", "guid-1"]
    client.request.assert_awaited_once_with(
        "DELETE", f"/resources/pkeys/{PKEY}/guids/guid-2,guid-1"
    )


async def test_empty_remove_is_noop_without_opening_client() -> None:
    client = _ufm_client()

    output = await remove_guids_from_pkey(RemoveGuidsInput(host=HOST, pkey=PKEY, guids=[]))

    assert output.guids_removed == []
    client.__aenter__.assert_not_awaited()


async def test_set_members_preserves_exact_payload_and_omits_index0() -> None:
    client = _ufm_client()
    activity_input = SetGuidsInput(
        host=HOST,
        pkey=PKEY,
        guids=["guid-1"],
        memberships=["limited"],
        ip_over_ib=False,
        index0=True,
    )

    output = await set_pkey_members(activity_input)

    assert output.guids_set == ["guid-1"]
    assert output.memberships_set == ["limited"]
    client.request.assert_awaited_once_with(
        "PUT",
        "/resources/pkeys/",
        json={
            "pkey": PKEY,
            "guids": ["guid-1"],
            "memberships": ["limited"],
            "ip_over_ib": False,
        },
    )


async def test_verify_members_is_case_insensitive_and_checks_membership() -> None:
    client = _ufm_client()
    client.request.return_value = {"guids": [{"guid": "GUID-1", "membership": "FULL"}]}

    output = await verify_pkey_members(
        VerifyPKeyMembersInput(
            host=HOST,
            pkey=PKEY,
            expected_guids=["guid-1"],
            expected_memberships=["full"],
            exact=True,
        )
    )

    assert output.verified is True
    assert output.present_guids == ["guid-1"]
    assert output.missing_guids == []


async def test_verify_members_missing_failure_remains_retryable() -> None:
    client = _ufm_client()
    client.request.return_value = {"guids": []}

    with pytest.raises(ApplicationError) as exc_info:
        await verify_pkey_members(
            VerifyPKeyMembersInput(host=HOST, pkey=PKEY, expected_guids=["guid-1"])
        )

    assert exc_info.value.non_retryable is False


async def test_verify_members_absent_treats_404_as_success() -> None:
    client = _ufm_client()
    client.request.side_effect = UFMClientError("not found", status_code=404)

    output = await verify_pkey_members_absent(
        VerifyPKeyMembersAbsentInput(host=HOST, pkey=PKEY, forbidden_guids=["guid-1"])
    )

    assert output.verified is True
    assert output.partition_exists is False
    assert output.remaining_member_count == 0
    assert output.still_present_guids == []


async def test_verify_members_absent_reports_remaining_unrelated_members() -> None:
    client = _ufm_client()
    client.request.return_value = {"guids": [{"guid": "guid-2", "membership": "full"}]}

    output = await verify_pkey_members_absent(
        VerifyPKeyMembersAbsentInput(host=HOST, pkey=PKEY, forbidden_guids=["guid-1"])
    )

    assert output.partition_exists is True
    assert output.remaining_member_count == 1
    assert output.still_present_guids == []
