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
"""Tests for InfiniBand PKey management activities."""

from configparser import ConfigParser
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from temporalio.exceptions import ApplicationError

from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_pkey import (
    CreatePKeyInput,
    ValidatePKeyInput,
    VerifyPKeyInput,
    _find_next_available_pkey,
    _parse_pkey_int,
    create_pkey_on_ufm,
    validate_pkey_available,
    verify_pkey_created,
)

UFM_BASE = "https://ufm.example.com/ufmRest"


def _create_config(sections: dict[str, dict[str, str]]) -> ConfigParser:
    config = ConfigParser()
    for section, values in sections.items():
        config.add_section(section)
        for key, value in values.items():
            config.set(section, key, value)
    return config


@pytest.fixture(autouse=True)
def reset_secrets_cache():
    clear_secrets_cache()
    yield
    clear_secrets_cache()


@pytest.fixture()
def mock_config():
    with patch("nv_config_manager.common.config.loader.load_config") as mock:
        mock.return_value = _create_config(
            {"ufm": {"ufm_api_user": "admin", "ufm_api_token_r1": "password"}}
        )
        yield mock


class TestParsePKeyInt:
    def test_standard_hex(self):
        assert _parse_pkey_int("0x8001") == 0x0001

    def test_full_pkey_with_high_bit(self):
        assert _parse_pkey_int("0xFFFF") == 0x7FFF

    def test_management_pkey(self):
        assert _parse_pkey_int("0x7fff") == 0x7FFF

    def test_low_pkey(self):
        assert _parse_pkey_int("0x0001") == 0x0001


class TestFindNextAvailablePKey:
    def test_empty_set_returns_min(self):
        result = _find_next_available_pkey(set(), 1, 100)
        assert result == 1

    def test_skips_existing(self):
        result = _find_next_available_pkey({1, 2, 3}, 1, 100)
        assert result == 4

    def test_skips_reserved(self):
        result = _find_next_available_pkey(set(), 0x7FFE, 0x7FFF)
        assert result == 0x7FFE

    def test_returns_none_when_full(self):
        all_used = set(range(1, 11))
        result = _find_next_available_pkey(all_used, 1, 10)
        assert result is None

    def test_finds_gap_in_middle(self):
        result = _find_next_available_pkey({1, 2, 4, 5}, 1, 10)
        assert result == 3


class TestValidatePKeyAvailable:
    @pytest.mark.asyncio
    async def test_specific_pkey_is_available(self, mock_config):
        with aioresponses() as m:
            m.get(
                f"{UFM_BASE}/resources/pkeys",
                payload={"0x7fff": {"partition": "management"}},
            )

            result = await validate_pkey_available(
                ValidatePKeyInput(host="ufm.example.com", pkey="0x8001")
            )

            assert result.pkey == "0x8001"
            assert result.auto_assigned is False

    @pytest.mark.asyncio
    async def test_specific_pkey_already_in_use(self, mock_config):
        with aioresponses() as m:
            m.get(
                f"{UFM_BASE}/resources/pkeys",
                payload={
                    "0x7fff": {"partition": "management"},
                    "0x8001": {"partition": "tenant-1"},
                },
            )

            with pytest.raises(ApplicationError, match="already in use"):
                await validate_pkey_available(
                    ValidatePKeyInput(host="ufm.example.com", pkey="0x8001")
                )

    @pytest.mark.asyncio
    async def test_auto_assign_finds_first_available(self, mock_config):
        with aioresponses() as m:
            m.get(
                f"{UFM_BASE}/resources/pkeys",
                payload={
                    "0x7fff": {"partition": "management"},
                    "0x0001": {"partition": "existing"},
                },
            )

            result = await validate_pkey_available(
                ValidatePKeyInput(host="ufm.example.com", pkey=None)
            )

            assert result.pkey == "0x0002"
            assert result.auto_assigned is True

    @pytest.mark.asyncio
    async def test_auto_assign_no_available_pkeys(self, mock_config):
        existing = {f"0x{i:04x}": {} for i in range(1, 4)}
        with aioresponses() as m:
            m.get(f"{UFM_BASE}/resources/pkeys", payload=existing)

            with pytest.raises(ApplicationError, match="No available PKeys"):
                await validate_pkey_available(
                    ValidatePKeyInput(
                        host="ufm.example.com",
                        pkey=None,
                        pkey_min=1,
                        pkey_max=3,
                    )
                )

    @pytest.mark.asyncio
    async def test_empty_ufm_response(self, mock_config):
        with aioresponses() as m:
            m.get(f"{UFM_BASE}/resources/pkeys", payload={})

            result = await validate_pkey_available(
                ValidatePKeyInput(host="ufm.example.com", pkey="0x8001")
            )

            assert result.pkey == "0x8001"
            assert result.existing_pkeys == []


class TestCreatePKeyOnUfm:
    @pytest.mark.asyncio
    async def test_creates_pkey_successfully(self, mock_config):
        with aioresponses() as m:
            m.post(f"{UFM_BASE}/resources/pkeys/add", payload={})

            result = await create_pkey_on_ufm(
                CreatePKeyInput(host="ufm.example.com", pkey="0x8001")
            )

            assert result.pkey == "0x8001"
            assert result.created is True

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self, mock_config):
        with aioresponses() as m:
            m.post(f"{UFM_BASE}/resources/pkeys/add", payload={})

            await create_pkey_on_ufm(
                CreatePKeyInput(
                    host="ufm.example.com",
                    pkey="0x8001",
                    ip_over_ib=False,
                )
            )

            request_key = next(iter(m.requests))
            request_body = m.requests[request_key][0].kwargs.get("json", {})
            assert request_body["pkey"] == "0x8001"
            assert request_body["ip_over_ib"] is False
            assert "guids" not in request_body
            assert "membership" not in request_body
            assert "index0" not in request_body

    @pytest.mark.asyncio
    async def test_deprecated_index0_not_sent(self, mock_config):
        """The deprecated index0 field is accepted for back-compat but never sent to UFM."""
        assert CreatePKeyInput(host="ufm.example.com", pkey="0x8001").index0 is None

        with aioresponses() as m:
            m.post(f"{UFM_BASE}/resources/pkeys/add", payload={})

            await create_pkey_on_ufm(
                CreatePKeyInput(host="ufm.example.com", pkey="0x8001", index0=True)
            )

            request_key = next(iter(m.requests))
            request_body = m.requests[request_key][0].kwargs.get("json", {})
            assert "index0" not in request_body


class TestVerifyPKeyCreated:
    @pytest.mark.asyncio
    async def test_pkey_found(self, mock_config):
        with aioresponses() as m:
            m.get(
                f"{UFM_BASE}/resources/pkeys/0x8001",
                payload={"partition": "new", "guids": []},
            )

            result = await verify_pkey_created(
                VerifyPKeyInput(host="ufm.example.com", pkey="0x8001")
            )

            assert result.verified is True
            assert result.pkey_data["partition"] == "new"

    @pytest.mark.asyncio
    async def test_pkey_not_found(self, mock_config):
        with aioresponses() as m:
            m.get(
                f"{UFM_BASE}/resources/pkeys/0x8001",
                payload={},
            )

            with pytest.raises(ApplicationError, match="not found"):
                await verify_pkey_created(VerifyPKeyInput(host="ufm.example.com", pkey="0x8001"))
