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
"""Convenience workflow operations over the generated Temporal HTTP SDK."""

from __future__ import annotations

from typing import Any, Self, cast

import aiohttp

from nv_config_manager_clients._base import HeaderProvider, ServiceClient
from nv_config_manager_clients._types import WhoamiResult
from nv_config_manager_clients.generated.temporal import ApiClient, Configuration
from nv_config_manager_clients.generated.temporal.api.default_api import DefaultApi
from nv_config_manager_clients.generated.temporal.api.workflow_api import WorkflowApi
from nv_config_manager_clients.generated.workflow_starts import WORKFLOW_STARTS


class TemporalClientException(Exception):
    """An unsuccessful workflow API request."""


class TemporalClient(ServiceClient):
    """HTTP API client; does not depend on the Temporal worker SDK."""

    api_client_type = ApiClient
    configuration_type = Configuration
    default_api_type = DefaultApi

    def __init__(
        self,
        base_url: str,
        user_domain: str,
        client_certificate: tuple[str, str] | None = None,
        headers: HeaderProvider = None,
        *,
        verify: bool | str = True,
    ) -> None:
        super().__init__(
            base_url, client_certificate=client_certificate, headers=headers, verify=verify
        )
        self.user_domain = user_domain
        self._api = WorkflowApi(self.api_client)

    @classmethod
    def for_mcp(
        cls,
        base_url: str,
        headers: HeaderProvider,
        user_domain: str = "nvidia.com",
    ) -> Self:
        """Create a caller-scoped HTTP client."""
        return cls(base_url, user_domain, headers=headers)

    async def whoami(self) -> WhoamiResult:
        """Return identity using the wrapper's public exception type."""
        try:
            return await super().whoami()
        except aiohttp.ClientError as exc:
            raise TemporalClientException(f"Failed to fetch whoami: {exc}") from exc

    async def list_workflows(self, params: dict[str, Any] | None = None) -> Any:
        """List executions using generated query serialization."""
        try:
            return await self._call(
                self._api.get_workflows_v1_workflow_get_without_preload_content, **(params or {})
            )
        except aiohttp.ClientError as exc:
            raise TemporalClientException(f"Failed to list workflows: {exc}") from exc

    async def get_workflow(self, workflow_id: str) -> Any:
        """Get an execution, safely encoding the workflow identifier."""
        try:
            return await self._call(
                self._api.get_workflow_v1_workflow_workflow_id_get_without_preload_content,
                workflow_id=workflow_id,
            )
        except aiohttp.ClientError as exc:
            raise TemporalClientException(f"Failed to get workflow: {exc}") from exc

    async def start_workflow(self, endpoint: str, payload: dict[str, Any]) -> Any:
        """Start a built-in or an endpoint added by a runtime-installed plugin."""
        try:
            if endpoint in WORKFLOW_STARTS:
                name, argument, model = WORKFLOW_STARTS[endpoint]
                return await self._call(
                    getattr(self._api, name + "_without_preload_content"),
                    **{argument: cast(Any, model).from_dict(payload)},
                )
            if (
                not endpoint.startswith("/")
                or any(part in {"", ".", ".."} for part in endpoint[1:].split("/"))
                or "?" in endpoint
                or "#" in endpoint
            ):
                raise ValueError("Expected a workflow endpoint such as /plugin/workflow")

            async def invoke_plugin(_request_timeout: float) -> Any:
                # Plugin endpoints are not knowable when the SDK is generated.
                # Reuse its serializer and HTTP client for this extension point.
                request = self.api_client.param_serialize(
                    method="POST",
                    resource_path="/v1/workflow" + endpoint,
                    body=payload,
                    header_params={"Content-Type": "application/json"},
                )
                response = await self.api_client.call_api(
                    *request, _request_timeout=_request_timeout
                )
                return response.response

            return await self._call(invoke_plugin)
        except (aiohttp.ClientError, ValueError) as exc:
            raise TemporalClientException(f"Failed to start workflow: {exc}") from exc

    async def invoke_backup_workflow(self, device_id: str, user: str = "nv-config-manager") -> str:
        """Start the generated backup operation and return its execution ID."""
        result = await self.start_workflow(
            "/ngc/backup",
            {
                "device_id": device_id,
                "trigger": "API",
                "user": user,
                "user_domain": self.user_domain,
                "workflow_id": None,
                "intended_config_commit_id": None,
            },
        )
        return cast(str, result["id"])
