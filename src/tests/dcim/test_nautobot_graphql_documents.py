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
"""Tests for grouped GraphQL documents owned by the Nautobot provider."""

import pytest
from aioresponses import aioresponses
from graphql import GraphQLSyntaxError
from nv_config_manager_dcim_nautobot_2x.client import NautobotClient, NautobotException
from nv_config_manager_dcim_nautobot_2x.queries import (
    _LOADING_GRAPHQL_SOURCES,
    _load_graphql_source,
    load_graphql_query,
    load_graphql_selection,
    render_graphql_fields_template,
)


def test_circular_graphql_import_has_an_actionable_error():
    """Recursive imports fail clearly instead of exhausting the Python stack."""
    filename = "circular-test.graphql"
    _LOADING_GRAPHQL_SOURCES.add(filename)
    try:
        with pytest.raises(ValueError, match="Circular GraphQL import detected"):
            _load_graphql_source(filename)
    finally:
        _LOADING_GRAPHQL_SOURCES.discard(filename)


def test_grouped_document_expands_reusable_fragments_and_selects_operation():
    """A grouped document carries the selected operation and imported fragments."""
    query = load_graphql_query("provider/events.graphql", "ListVRFAffectedDevices")

    assert query.operation_name == "ListVRFAffectedDevices"
    assert "fragment DeviceRenderStatus on DeviceType" in query
    assert "query ListVRFAffectedDevices" in query


@pytest.mark.asyncio
async def test_rest_errors_expose_the_http_status_code():
    """Callers can branch on HTTP status without parsing exception text."""
    with aioresponses() as mocked:
        mocked.get("https://nautobot.example/api/missing/", status=404, payload={"detail": "No"})
        async with NautobotClient("https://nautobot.example", token="token") as client:
            with pytest.raises(NautobotException) as caught:
                await client.get("missing/")

    assert caught.value.status_code == 404


def test_selection_fragment_is_validated_for_dynamic_legacy_queries():
    """The compatibility field-selection extension point remains syntax checked."""
    fields = load_graphql_selection("network_device_fields.graphql")

    assert "configmanagerdevicestatus" in fields


def test_render_location_query_excludes_specialty_topology_roles():
    """Provider baseline data must not require optional template-plugin roles."""
    query = load_graphql_query("query_location_data.graphql")

    assert "FNN-Switch" not in query


def test_dynamic_fields_are_validated_after_template_expansion():
    """The legacy field extension point is still rejected when it is invalid GraphQL."""
    with pytest.raises(GraphQLSyntaxError, match="Syntax Error"):
        render_graphql_fields_template("device_by_id.graphql", "invalid {")


@pytest.mark.asyncio
async def test_transport_submits_selected_operation_name():
    """Grouped documents use GraphQL's standard operationName transport field."""
    query = load_graphql_query("provider/devices.graphql", "GetDeviceMetadata")
    with aioresponses() as mocked:
        mocked.post("https://nautobot.example/api/graphql/", payload={"data": {}})
        async with NautobotClient("https://nautobot.example", token="token") as client:
            await client.graphql_query(query, {"id": "device-id"})

    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["json"]["operationName"] == "GetDeviceMetadata"
    assert "query GetDeviceMetadata" in request.kwargs["json"]["query"]
    assert "query GetZTPDevice" not in request.kwargs["json"]["query"]
