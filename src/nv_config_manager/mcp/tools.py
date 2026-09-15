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
"""MCP tool registration for NVIDIA Config Manager."""

from __future__ import annotations

import inspect
from typing import Annotated, Any, cast

from mcp.server.fastmcp import FastMCP

from nv_config_manager.mcp.clients import (
    MCPClientError,
    clamp_limit,
    fetch_config_diff,
    fetch_config_versions,
    fetch_device_config,
    fetch_device_configs,
    fetch_dhcp_config,
    fetch_workflow_detail,
    fetch_workflows,
    nautobot_graphql_query,
    nautobot_rest_get,
    start_workflow,
)
from nv_config_manager.mcp.settings import MCPSettings
from nv_config_manager.mcp.workflows import (
    MCPWorkflow,
    allows_none,
    discover_mcp_workflows,
    normalize_workflow_parameters,
)

PUBLIC_DOCS_MCP_SERVER_URL = (
    "https://docs.nvidia.com/switch-infrastructure/config-manager/_mcp/server"
)


def register_tools(server: FastMCP, settings: MCPSettings) -> None:
    """Register NVIDIA Config Manager MCP tools."""

    def nautobot_tool() -> Any:
        """Register an optional tool when the selected provider supports it."""
        if settings.nautobot_mcp_enabled:
            return server.tool()
        return lambda function: function

    @server.tool()
    async def list_related_mcp_servers() -> dict[str, Any]:
        """List related MCP servers that clients can connect to directly."""
        return {
            "servers": [
                {
                    "name": "nvidia-config-manager-public-docs",
                    "url": PUBLIC_DOCS_MCP_SERVER_URL,
                    "purpose": (
                        "Public NVIDIA Config Manager documentation. Use this server for "
                        "product documentation, usage guidance, and conceptual reference."
                    ),
                    "authentication": "none",
                    "notes": (
                        "Connect the MCP-capable client directly to this URL; the operational "
                        "Config Manager MCP server does not proxy public documentation tools."
                    ),
                }
            ]
        }

    @nautobot_tool()
    async def search_devices(
        query: str | None = None,
        hostname: str | None = None,
        serial: str | None = None,
        mac_address: str | None = None,
        device_id: str | None = None,
        cf_nico_machine_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search Nautobot devices by common operator identifiers."""
        bounded_limit = clamp_limit(limit)

        if mac_address:
            gql_query = """
                query ($mac_address: [String]) {
                  interfaces(mac_address: $mac_address) {
                    id
                    name
                    mac_address
                    device {
                      id
                      name
                      serial
                      role { name }
                      platform { name }
                      location { name }
                    }
                    module {
                      device {
                        id
                        name
                        serial
                        role { name }
                        platform { name }
                        location { name }
                      }
                    }
                  }
                }
            """
            return await nautobot_graphql_query(settings, gql_query, {"mac_address": [mac_address]})

        params: dict[str, Any] = {"limit": bounded_limit}
        if query:
            params["q"] = query
        if hostname:
            params["name"] = hostname
        if serial:
            params["serial"] = serial
        if device_id:
            params["id"] = device_id
        if cf_nico_machine_id:
            params["cf_nico_machine_id"] = cf_nico_machine_id
        if len(params) == 1:
            raise MCPClientError("At least one search identifier is required.")
        return await nautobot_rest_get(settings, "dcim/devices/", params=params)

    @nautobot_tool()
    async def get_device_id(
        query: str | None = None,
        hostname: str | None = None,
        serial: str | None = None,
        mac_address: str | None = None,
        cf_nico_machine_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a device identifier to a Nautobot device ID."""
        result = await search_devices(
            query=query,
            hostname=hostname,
            serial=serial,
            mac_address=mac_address,
            cf_nico_machine_id=cf_nico_machine_id,
            limit=2,
        )
        devices = _extract_devices(result.get("data"))
        if not devices:
            return {"found": False, "matches": []}
        if len(devices) > 1:
            return {"found": False, "ambiguous": True, "matches": devices}
        return {"found": True, "device": devices[0], "device_id": devices[0].get("id")}

    @nautobot_tool()
    async def query_nautobot(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a read-only Nautobot GraphQL query."""
        return await nautobot_graphql_query(settings, query, variables)

    @nautobot_tool()
    async def list_nautobot_types(
        name_contains: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List Nautobot GraphQL schema types."""
        gql_query = """
            query {
              __schema {
                types {
                  name
                  kind
                  description
                }
              }
            }
        """
        result = await nautobot_graphql_query(settings, gql_query)
        upstream_truncated = bool(result.get("truncated", False))
        types = result.get("data", {}).get("data", {}).get("__schema", {}).get("types", [])
        if name_contains:
            needle = name_contains.lower()
            types = [item for item in types if needle in str(item.get("name", "")).lower()]
        return {"truncated": upstream_truncated, "data": {"types": types[: clamp_limit(limit)]}}

    @nautobot_tool()
    async def get_nautobot_type(type_name: str) -> dict[str, Any]:
        """Inspect a Nautobot GraphQL schema type."""
        gql_query = """
            query ($type_name: String!) {
              __type(name: $type_name) {
                name
                kind
                description
                fields {
                  name
                  description
                  type { kind name ofType { kind name ofType { kind name } } }
                }
                inputFields {
                  name
                  description
                  type { kind name ofType { kind name ofType { kind name } } }
                }
                enumValues {
                  name
                  description
                }
              }
            }
        """
        return await nautobot_graphql_query(settings, gql_query, {"type_name": type_name})

    @server.tool()
    async def get_dhcp_config(ip_version: int = 4) -> dict[str, Any]:
        """Get the sanitized running DHCP configuration."""
        if ip_version not in {4, 6}:
            raise MCPClientError("ip_version must be 4 or 6.")
        return await fetch_dhcp_config(settings, ip_version)

    @server.tool()
    async def list_device_configs(
        device_id: str,
        file_type: str | None = "intended",
    ) -> dict[str, Any]:
        """Return a bounded response whose data is a list of Config Store files."""
        return await fetch_device_configs(settings, device_id, file_type=file_type)

    @server.tool()
    async def get_device_config(
        device_id: str,
        filename: str,
        file_type: str = "intended",
        version: int | None = None,
    ) -> dict[str, Any]:
        """Get a redacted configuration file from Config Store."""
        return await fetch_device_config(settings, device_id, filename, file_type, version)

    @server.tool()
    async def get_config_versions(
        device_id: str,
        filename: str,
        file_type: str = "intended",
        limit: int = 100,
    ) -> dict[str, Any]:
        """List Config Store versions for a device config file."""
        return await fetch_config_versions(
            settings,
            device_id,
            filename,
            file_type=file_type,
            limit=clamp_limit(limit),
        )

    @server.tool()
    async def get_config_diff(
        device_id: str,
        filename: str,
        from_version: int,
        to_version: int,
        file_type: str = "intended",
    ) -> dict[str, Any]:
        """Get a redacted diff between two Config Store versions."""
        return await fetch_config_diff(
            settings,
            device_id,
            filename,
            from_version,
            to_version,
            file_type=file_type,
        )

    @server.tool()
    async def list_workflows(
        user: str | None = None,
        workflow_type: str | None = None,
        device_id: str | None = None,
        device_name: str | None = None,
        site: str | None = None,
        status: str | None = None,
        limit: int = 50,
        next_page_token: str | None = None,
    ) -> dict[str, Any]:
        """List Workflow API executions visible to the caller."""
        params = _drop_none(
            {
                "user": user,
                "workflow_type": workflow_type,
                "device_id": device_id,
                "device_name": device_name,
                "site": site,
                "status": status,
                "limit": clamp_limit(limit),
                "next_page_token": next_page_token,
            }
        )
        return await fetch_workflows(settings, params)

    @server.tool()
    async def get_workflow_detail(workflow_id: str) -> dict[str, Any]:
        """Get details for a Workflow API execution visible to the caller."""
        return await fetch_workflow_detail(settings, workflow_id)

    for workflow in discover_mcp_workflows():
        _register_workflow_starter(server, settings, workflow)


def _register_workflow_starter(
    server: FastMCP, settings: MCPSettings, workflow: MCPWorkflow
) -> None:
    async def run_workflow(**parameters: Any) -> dict[str, Any]:
        """Start a safe diagnostic workflow."""
        normalized = normalize_workflow_parameters(workflow, parameters)
        result = await start_workflow(settings, workflow.endpoint, normalized)
        workflow_result = result.get("data") if isinstance(result, dict) else None
        workflow_id = workflow_result.get("id") if isinstance(workflow_result, dict) else None
        workflow_ui_href = (
            workflow_result.get("ui_href") if isinstance(workflow_result, dict) else None
        )
        return {
            "workflow": workflow.workflow_name,
            "endpoint": workflow.endpoint,
            "workflow_id": workflow_id,
            "workflow_ui_href": workflow_ui_href,
            "input_schema": workflow.input_schema,
            "result": result,
        }

    run_workflow.__name__ = workflow.tool_name
    cast(Any, run_workflow).__signature__ = _workflow_tool_signature(workflow)
    run_workflow.__doc__ = (
        f"{workflow.tool_description}\n\n"
        "Pass the workflow input fields shown in this tool's input schema. "
        "Use `workflow_ui_href` as the end-user Config Manager workflow link. "
        "The response includes the workflow input schema for reference."
    )
    server.tool(name=workflow.tool_name, description=workflow.tool_description)(run_workflow)


def _workflow_tool_signature(workflow: MCPWorkflow) -> inspect.Signature:
    """Build the workflow-specific signature FastMCP uses for its input schema."""
    parameters: list[inspect.Parameter] = []
    for field_name, field_info in workflow.input_class.model_fields.items():
        default: Any = inspect.Parameter.empty
        if field_name == "trigger":
            default = "API"
        elif allows_none(field_info.annotation):
            default = None
        elif not field_info.is_required():
            default = field_info

        parameters.append(
            inspect.Parameter(
                field_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=Annotated[field_info.annotation, field_info],
            )
        )

    return inspect.Signature(parameters, return_annotation=dict[str, Any])


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _extract_devices(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return [cast(dict[str, Any], result) for result in results if isinstance(result, dict)]
    if isinstance(data, dict):
        interfaces = data.get("interfaces")
        if not isinstance(interfaces, list):
            return []
        devices: dict[str, dict[str, Any]] = {}
        for interface in interfaces:
            if not isinstance(interface, dict):
                continue
            module = interface.get("module")
            device = interface.get("device")
            if not isinstance(device, dict) and isinstance(module, dict):
                device = module.get("device")
            device_id = device.get("id") if isinstance(device, dict) else None
            if device_id:
                devices[str(device_id)] = cast(dict[str, Any], device)
        return list(devices.values())
    return []
