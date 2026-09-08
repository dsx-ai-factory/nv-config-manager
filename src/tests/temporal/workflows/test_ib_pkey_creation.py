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
"""Tests for InfiniBand PKey Creation Workflow."""

import re
import uuid
from configparser import ConfigParser
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from temporalio.worker import Worker

from nv_config_manager.temporal.common.secrets import clear_secrets_cache
from nv_config_manager.temporal.ngc.activities.ib_nautobot import (
    record_ib_pkey_in_dcim,
    record_ib_pkey_in_nautobot,
    resolve_ib_site_for_host,
)
from nv_config_manager.temporal.ngc.activities.ib_pkey import (
    create_pkey_on_ufm,
    validate_pkey_available,
    verify_pkey_created,
)
from nv_config_manager.temporal.ngc.activities.nats import publish_nats
from nv_config_manager.temporal.ngc.workflows.ib_pkey_creation import (
    IBPKeyCreationInput,
    IBPKeyCreationWorkflow,
    IBPKeyCreationWorkflowOutput,
)

UFM_BASE = "https://ufm.example.com/ufmRest"
NB_URL = "https://nautobot.example.com"
NB_API = f"{NB_URL}/api"
NB_GRAPHQL = f"{NB_API}/graphql/"
PLUGIN = f"{NB_API}/plugins/overlays"


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


@pytest.fixture(autouse=True)
def _mock_nats():
    """Prevent real NATS connections from ArchiveMixin."""
    mock_producer = AsyncMock()
    mock_producer.__aenter__ = AsyncMock(return_value=mock_producer)
    mock_producer.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "nv_config_manager.temporal.ngc.activities.nats.NatsProducer",
        return_value=mock_producer,
    ):
        yield


@pytest.fixture()
def mock_configs():
    """Mock both UFM and Nautobot config loading."""
    ufm_cfg = _create_config({"ufm": {"ufm_api_user": "admin", "ufm_api_token_r1": "password"}})
    nb_cfg = _create_config(
        {
            "dcim": {
                "provider": "nautobot-2x",
                "server": NB_URL,
                "token": "test-token",
                "verify": "false",
            },
            "nats": {},
        }
    )
    with (
        patch("nv_config_manager.temporal.client.ufm.load_config", return_value=ufm_cfg),
        patch("nv_config_manager.common.config.load_config", return_value=nb_cfg),
    ):
        yield


def _stub_nautobot_record_pkey(
    m: aioresponses,
    pkey: str,
    *,
    existing_orphan_id: str | None = None,
    new_pkey_id: str = "pkey-1",
) -> None:
    """Stub the Nautobot endpoints the record-pkey activity hits."""
    m.get(
        re.compile(rf"{re.escape(NB_API)}/extras/statuses/.*"),
        payload={"results": [{"id": "stat-1", "name": "Active"}]},
    )
    if existing_orphan_id is not None:
        m.get(
            re.compile(rf"{re.escape(PLUGIN)}/pkeys/.*"),
            payload={"results": [{"id": existing_orphan_id, "pkey": pkey, "overlay": None}]},
        )
    else:
        m.get(
            re.compile(rf"{re.escape(PLUGIN)}/pkeys/.*"),
            payload={"results": []},
        )
        m.post(f"{PLUGIN}/pkeys/", payload={"id": new_pkey_id, "pkey": pkey})


def _stub_nautobot_resolve_site(
    m: aioresponses,
    *,
    site_name: str = "test-site",
    site_id: str = "site-1",
) -> None:
    """Stub the GraphQL device-by-name query that the site resolver hits."""
    m.post(
        NB_GRAPHQL,
        payload={
            "data": {
                "devices": [
                    {
                        "id": "dev-1",
                        "name": "ufm.example.com",
                        "role": {"name": "UFM"},
                        "primary_ip4": {"host": "10.0.0.1"},
                        "location": {
                            "id": site_id,
                            "name": site_name,
                            "location_type": {"name": "Site"},
                        },
                    }
                ]
            }
        },
    )


def _stub_ufm_create_flow(m: aioresponses, *, verify_pkey: str | None = None) -> None:
    """Stub the UFM endpoints the validate/create/verify stages hit."""
    m.get(
        f"{UFM_BASE}/resources/pkeys",
        payload={"0x7fff": {"partition": "management"}},
    )
    m.post(f"{UFM_BASE}/resources/pkeys/add", payload={})
    if verify_pkey is None:
        m.get(
            re.compile(rf"{re.escape(UFM_BASE)}/resources/pkeys/0x[0-9a-fA-F]+$"),
            payload={"partition": "auto-assigned", "guids": []},
        )
    else:
        m.get(
            f"{UFM_BASE}/resources/pkeys/{verify_pkey}",
            payload={"partition": "new-tenant", "guids": []},
        )


CREATION_ACTIVITIES = [
    resolve_ib_site_for_host,
    validate_pkey_available,
    create_pkey_on_ufm,
    verify_pkey_created,
    record_ib_pkey_in_dcim,
    record_ib_pkey_in_nautobot,
    publish_nats,
]


def test_creation_output_accepts_legacy_pkey_identifier() -> None:
    """Results written before the neutral alias remain deserializable."""
    result = IBPKeyCreationWorkflowOutput.model_validate(
        {
            "pkey": "0x1234",
            "auto_assigned": False,
            "created": True,
            "verified": True,
            "pkey_data": {},
            "nautobot_pkey_id": "pkey-1",
        }
    )

    assert result.dcim_pkey_id == "pkey-1"


@pytest.mark.asyncio
async def test_ib_pkey_creation_with_specific_pkey(mock_configs, time_skipping_env):
    """Full workflow: validate, create, verify, record with a specific PKey."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyCreationWorkflow],
            activities=CREATION_ACTIVITIES,
        ):
            with aioresponses() as m:
                _stub_nautobot_resolve_site(m)
                _stub_ufm_create_flow(m, verify_pkey="0x8001")
                _stub_nautobot_record_pkey(m, "0x8001")

                result = await env.client.execute_workflow(
                    IBPKeyCreationWorkflow.run,
                    IBPKeyCreationInput(host="ufm.example.com", pkey="0x8001"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

            assert isinstance(result, IBPKeyCreationWorkflowOutput)
            assert result.pkey == "0x8001"
            assert result.auto_assigned is False
            assert result.created is True
            assert result.verified is True
            assert result.dcim_pkey_id == "pkey-1"
            assert result.nautobot_pkey_id == "pkey-1"


@pytest.mark.asyncio
async def test_ib_pkey_creation_with_auto_assign(mock_configs, time_skipping_env):
    """Full workflow: auto-assign PKey when none specified."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyCreationWorkflow],
            activities=CREATION_ACTIVITIES,
        ):
            with aioresponses() as m:
                _stub_nautobot_resolve_site(m)
                _stub_ufm_create_flow(m)
                _stub_nautobot_record_pkey(m, "0x0001")

                result = await env.client.execute_workflow(
                    IBPKeyCreationWorkflow.run,
                    IBPKeyCreationInput(host="ufm.example.com", pkey=None),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

            assert result.pkey == "0x0001"
            assert result.auto_assigned is True
            assert result.created is True
            assert result.verified is True
            assert result.nautobot_pkey_id == "pkey-1"


@pytest.mark.asyncio
async def test_ib_pkey_creation_reuses_existing_orphan_pkey(mock_configs, time_skipping_env):
    """If an orphan PKey row already exists, the workflow reuses its id."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyCreationWorkflow],
            activities=CREATION_ACTIVITIES,
        ):
            with aioresponses() as m:
                _stub_nautobot_resolve_site(m)
                _stub_ufm_create_flow(m, verify_pkey="0x8001")
                _stub_nautobot_record_pkey(m, "0x8001", existing_orphan_id="existing-pkey-id")

                result = await env.client.execute_workflow(
                    IBPKeyCreationWorkflow.run,
                    IBPKeyCreationInput(host="ufm.example.com", pkey="0x8001"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

            assert result.nautobot_pkey_id == "existing-pkey-id"


@pytest.mark.asyncio
async def test_ib_pkey_creation_site_override_skips_nautobot_resolve(
    mock_configs, time_skipping_env
):
    """An explicit ``site`` makes resolve_context skip the Nautobot lookup."""
    task_queue = str(uuid.uuid4())

    async with time_skipping_env() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[IBPKeyCreationWorkflow],
            activities=CREATION_ACTIVITIES,
        ):
            with aioresponses() as m:
                # Intentionally NO _stub_nautobot_resolve_site -- if the resolver
                # were to fire, the GraphQL call would 500 from aioresponses.
                _stub_ufm_create_flow(m, verify_pkey="0x8001")
                _stub_nautobot_record_pkey(m, "0x8001")

                result = await env.client.execute_workflow(
                    IBPKeyCreationWorkflow.run,
                    IBPKeyCreationInput(
                        host="ufm.example.com", pkey="0x8001", site="explicit-site"
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

            assert result.verified is True
            assert result.nautobot_pkey_id == "pkey-1"
