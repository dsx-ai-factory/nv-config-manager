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
"""NVIDIA Config Manager MCP Streamable HTTP server."""

from __future__ import annotations

import argparse

import uvicorn
from mcp.server.fastmcp import FastMCP
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from nv_config_manager.common.config_watch import restart_on_config_change
from nv_config_manager.common.log import configure_logging
from nv_config_manager.common.telemetry import instrument_asgi_app, setup_tracing
from nv_config_manager.mcp.auth import (
    DEFAULT_MCP_UNAUTHENTICATED_PATHS,
    RequestAuthMiddleware,
    ServiceAuthMiddleware,
)
from nv_config_manager.mcp.oauth_metadata import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from nv_config_manager.mcp.oauth_proxy import (
    proxy_authorization_callback,
    proxy_authorization_request,
    proxy_token_request,
)
from nv_config_manager.mcp.settings import (
    AUTHORIZATION_SERVER_METADATA_PATH,
    OAUTH_AUTHORIZE_PATH,
    OAUTH_CALLBACK_PATH,
    OAUTH_TOKEN_PATH,
    MCPOAuthSettings,
    MCPSettings,
)
from nv_config_manager.mcp.tools import register_tools

configure_logging(service="mcp")
setup_tracing("nv-config-manager-mcp")


def create_mcp_server(
    settings: MCPSettings | None = None,
    oauth_settings: MCPOAuthSettings | None = None,
) -> FastMCP:
    """Create the FastMCP server and register tools."""
    resolved_settings = settings or MCPSettings.from_config()
    resolved_oauth_settings = oauth_settings or MCPOAuthSettings.from_config()
    server = FastMCP(
        "nv-config-manager-mcp",
        instructions=(
            "Read-only NVIDIA Config Manager operator tools plus explicitly "
            "enabled safe diagnostic workflow starters. When auth is enabled, tools "
            "use the caller's Bearer token for Config Manager APIs. Nautobot auth "
            "mode is configured per environment. Use list_related_mcp_servers to "
            "discover public documentation MCP servers that clients can connect to "
            "directly."
        ),
        host="0.0.0.0",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/mcp",
    )

    @server.custom_route("/healthcheck", methods=["GET"], include_in_schema=False)
    async def healthcheck(request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy"})

    @server.custom_route("/metrics", methods=["GET"], include_in_schema=False)
    async def metrics(request: Request) -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if resolved_oauth_settings.enabled:
        _register_oauth_metadata_routes(server, resolved_oauth_settings)

    register_tools(server, resolved_settings)
    return server


def create_app(
    settings: MCPSettings | None = None,
    oauth_settings: MCPOAuthSettings | None = None,
) -> ASGIApp:
    """Create the ASGI application for Streamable HTTP MCP."""
    resolved_oauth_settings = oauth_settings or MCPOAuthSettings.from_config()
    unauthenticated_paths = DEFAULT_MCP_UNAUTHENTICATED_PATHS
    resource_metadata_url = ""
    if resolved_oauth_settings.enabled:
        resource_metadata_url = resolved_oauth_settings.resource_metadata_url
        unauthenticated_paths = (
            unauthenticated_paths
            | resolved_oauth_settings.well_known_paths
            | resolved_oauth_settings.oauth_proxy_paths
        )
    return instrument_asgi_app(
        ServiceAuthMiddleware(
            RequestAuthMiddleware(
                create_mcp_server(settings, resolved_oauth_settings).streamable_http_app()
            ),
            unauthenticated_paths=unauthenticated_paths,
            resource_metadata_url=resource_metadata_url,
        )
    )


def _register_oauth_metadata_routes(server: FastMCP, settings: MCPOAuthSettings) -> None:
    async def oauth_protected_resource_metadata(
        request: Request,
    ) -> JSONResponse | Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)
        return JSONResponse(
            protected_resource_metadata(settings),
            headers={"Cache-Control": "no-store"},
        )

    for metadata_path in settings.well_known_paths - {AUTHORIZATION_SERVER_METADATA_PATH}:
        server.custom_route(
            metadata_path,
            methods=["GET", "OPTIONS"],
            include_in_schema=False,
        )(oauth_protected_resource_metadata)

    @server.custom_route(
        AUTHORIZATION_SERVER_METADATA_PATH,
        methods=["GET", "OPTIONS"],
        include_in_schema=False,
    )
    async def oauth_authorization_server_metadata(
        request: Request,
    ) -> JSONResponse | Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)
        return JSONResponse(
            authorization_server_metadata(settings),
            headers={"Cache-Control": "no-store"},
        )

    if not settings.forward_resource_parameter:

        @server.custom_route(
            OAUTH_AUTHORIZE_PATH,
            methods=["GET"],
            include_in_schema=False,
        )
        async def oauth_authorize_proxy(request: Request) -> Response:
            return await proxy_authorization_request(request, settings)

        @server.custom_route(
            OAUTH_CALLBACK_PATH,
            methods=["GET"],
            include_in_schema=False,
        )
        async def oauth_callback_proxy(request: Request) -> Response:
            return await proxy_authorization_callback(request)

        @server.custom_route(
            OAUTH_TOKEN_PATH,
            methods=["POST"],
            include_in_schema=False,
        )
        async def oauth_token_proxy(request: Request) -> Response:
            return await proxy_token_request(request, settings)


def main() -> None:
    """CLI entrypoint for the MCP service."""
    parser = argparse.ArgumentParser(description="NVIDIA Config Manager MCP Server")
    parser.add_argument("--port", type=int, default=9000, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    restart_on_config_change()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        log_config=None,
        loop="asyncio",
    )
