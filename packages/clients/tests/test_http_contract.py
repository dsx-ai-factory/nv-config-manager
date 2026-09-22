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
"""Exercise wrappers and generated clients together against a local HTTP server."""

import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import web
from nv_config_manager_logging import LogCategory

from nv_config_manager_clients import (
    ConfigStoreClient,
    ConfigStoreException,
    ConfigStoreFileNotFound,
    DHCPClient,
    DHCPClientException,
    RenderClient,
    TemporalClient,
    TemporalClientException,
    ZTPClient,
    ZTPClientException,
)
from nv_config_manager_clients.generated.config_store import ApiClient, Configuration
from nv_config_manager_clients.generated.config_store.api.default_api import DefaultApi
from nv_config_manager_clients.generated.ztp import ApiClient as ZTPApiClient
from nv_config_manager_clients.generated.ztp import Configuration as ZTPConfiguration
from nv_config_manager_clients.generated.ztp.api.files_api import FilesApi


@dataclass
class Server:
    url: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)
    responses: list[tuple[int, Any]] = field(default_factory=list)

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.raw_path,
                "query": dict(request.query),
                "headers": dict(request.headers),
                "body": await request.json() if request.can_read_body else None,
            }
        )
        status, body = self.responses.pop(0)
        if isinstance(body, bytes):
            return web.Response(status=status, body=body, content_type="application/octet-stream")
        if isinstance(body, str):
            return web.Response(status=status, text=body)
        return web.json_response(body, status=status)


@pytest.fixture
async def server(unused_tcp_port: int) -> AsyncIterator[Server]:
    state = Server(url=f"http://127.0.0.1:{unused_tcp_port}")
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", state.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    try:
        yield state
    finally:
        await runner.cleanup()


async def test_config_store_paths_queries_and_return_models(server: Server) -> None:
    server.responses = [
        (200, {"content": "config", "version": 5, "content_hash": "hash"}),
        (200, []),
        (200, {"versions": []}),
        (200, {"diff": "changed"}),
    ]
    async with ConfigStoreClient(server.url, "intended", server.url) as client:
        result = await client.load_file("device id", "dir/startup#.cfg")
        assert result.content == "config"
        assert result.commit == "5"
        await client.list_device_configs("device", file_type=None)
        await client.get_config_versions("device", "startup.cfg", limit=3)
        await client.get_config_diff("device", "startup.cfg", 1, 2, file_type="backup")
    assert (
        server.requests[0]["path"]
        == "/v1/config/device%20id/dir%2Fstartup%23.cfg?file_type=intended"
    )
    assert server.requests[1]["query"] == {}
    assert server.requests[2]["query"] == {"file_type": "intended", "limit": "3"}
    assert server.requests[3]["query"] == {
        "file_type": "backup",
        "from_version": "1",
        "to_version": "2",
    }
    assert client.api_client.rest_client.pool_manager._client.closed


async def test_batch_skips_unchanged_and_serializes_generated_request(server: Server) -> None:
    server.responses = [
        (200, {"content": "same", "version": 1, "content_hash": "hash"}),
        (404, {"detail": "missing"}),
        (200, {"created": [{"version": 2}]}),
    ]
    async with ConfigStoreClient(server.url, "backup", server.url) as client:
        files = await client.persist_files(
            "device", {"same.cfg": "same", "new.cfg.j2": "new"}, "backup", "user", "example.com"
        )
    assert [item.filename for item in files] == ["new.cfg"]
    assert server.requests[-1]["path"] == "/v1/config/device/batch"
    assert server.requests[-1]["body"] == {
        "files": [
            {
                "filename": "new.cfg",
                "content": "new",
                "author": "user@example.com",
                "commit_message": "backup",
                "file_type": "backup",
            }
        ]
    }


async def test_batch_maps_created_versions_after_server_skips(server: Server) -> None:
    server.responses = [
        (404, {"detail": "missing"}),
        (404, {"detail": "missing"}),
        (
            200,
            {
                "created": [{"version": 8}],
                "skipped": ["boot-script"],
            },
        ),
    ]
    async with ConfigStoreClient(server.url, "intended", server.url) as client:
        files = await client.persist_files(
            "device",
            {"boot-script": "boot content", "startup.yaml": "startup content"},
            "render",
            "user",
            "example.com",
        )

    assert files is not None
    assert [(item.filename, item.commit) for item in files] == [("startup.yaml", "8")]


async def test_auth_refreshes_on_retries_and_subsequent_requests(server: Server) -> None:
    identity = {"user": "worker", "roles": ["service"]}
    server.responses = [(503, {"detail": "retry"}), (200, identity), (200, identity)]
    counter = 0

    def headers() -> dict[str, str]:
        nonlocal counter
        counter += 1
        return {"Authorization": f"Bearer test-token-{counter}"}

    async with ConfigStoreClient(server.url, "intended", server.url, headers=headers) as client:
        client.retry_options._start_timeout = 0.001
        assert await client.whoami() == identity
        assert await client.whoami() == identity
    assert [r["headers"]["Authorization"] for r in server.requests] == [
        "Bearer test-token-1",
        "Bearer test-token-2",
        "Bearer test-token-3",
    ]
    assert all(r["path"] == "/whoami" for r in server.requests)


async def test_public_generated_client_works_without_wrapper(server: Server) -> None:
    server.responses = [(200, {"user": "external", "roles": ["reader"]})]
    configuration = Configuration(host=server.url, access_token="test-token")
    async with ApiClient(configuration) as client:
        identity = await DefaultApi(client).whoami_whoami_get()
    assert identity.user == "external"
    assert server.requests[0]["headers"]["Authorization"] == "Bearer test-token"


def test_mtls_requires_tls_1_3() -> None:
    """All generated transports retain the previous mTLS protocol floor."""
    with patch.object(ssl.SSLContext, "load_cert_chain"):
        clients = [
            RenderClient("https://render.example.test", ("client.crt", "client.key")),
            TemporalClient(
                "https://temporal.example.test",
                "example.com",
                ("client.crt", "client.key"),
            ),
            ZTPClient("https://ztp.example.test", ("client.crt", "client.key")),
        ]
    assert all(
        client.api_client.rest_client.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_3
        for client in clients
    )


def test_clients_preserve_structured_log_categories() -> None:
    """Extracted wrappers keep the category labels used by dashboards."""
    assert ConfigStoreClient.logger.extra["category"] == LogCategory.CONFIG_STORE
    assert RenderClient.logger.extra["category"] == LogCategory.RENDER
    assert TemporalClient.logger.extra["category"] == LogCategory.TEMPORAL_ACTIVITY


def test_temporal_auth_headers_require_https() -> None:
    """Public callers cannot accidentally transmit authentication over plaintext HTTP."""
    with pytest.raises(ValueError, match="require HTTPS"):
        TemporalClient(
            "http://temporal.example.test", "example.com", headers={"Authorization": "x"}
        )


async def test_render_retry_and_payload(server: Server) -> None:
    server.responses = [
        (409, {"detail": "busy"}),
        (200, {"updated_files": [{"filename": "a", "commit": "9"}]}),
    ]
    async with RenderClient(server.url) as client:
        client.retry_options._start_timeout = 0.001
        files = await client.execute_render("device", "wf")
    assert files[0].commit == "9"
    assert len(server.requests) == 2
    assert server.requests[0]["path"] == "/v1/render/device/render"
    assert server.requests[0]["body"]["commit_message"] == "Render triggered by workflow wf"


async def test_dhcp_and_temporal_operations(server: Server) -> None:
    server.responses = [
        (200, {"config": {}}),
        (200, {"workflows": []}),
        (200, {"id": "wf/id"}),
        (200, {"id": "backup"}),
        (200, {"id": "plugin"}),
    ]
    async with DHCPClient(server.url) as dhcp:
        assert await dhcp.get_config(6) == {"config": {}}
    async with TemporalClient(server.url, "example.com") as temporal:
        await temporal.list_workflows({"limit": 5, "hide_completed": True})
        await temporal.get_workflow("wf/id")
        assert await temporal.invoke_backup_workflow("device") == "backup"
        assert await temporal.start_workflow("/plugin/start", {"custom": "value"}) == {
            "id": "plugin"
        }
    assert server.requests[0]["query"] == {"ip_version": "6"}
    assert server.requests[1]["query"] == {"limit": "5", "hide_completed": "true"}
    assert server.requests[2]["path"] == "/v1/workflow/wf%2Fid"
    assert server.requests[3]["path"] == "/v1/workflow/ngc/backup"
    assert server.requests[3]["body"]["device_id"] == "device"
    assert server.requests[4]["body"] == {"custom": "value"}


async def test_generated_ztp_download_preserves_binary_content(server: Server) -> None:
    content = b"\x00\xff\x80firmware\r\n"
    server.responses = [(200, content)]
    configuration = ZTPConfiguration(host=server.url)

    async with ZTPApiClient(configuration) as client:
        downloaded = await FilesApi(client).load_object_v1_files_platform_version_filename_get(
            platform="platform", version="1.0", filename="firmware.bin"
        )

    assert downloaded == content
    assert server.requests[0]["path"] == "/v1/files/platform/1.0/firmware.bin"


async def test_ztp_uses_head_and_preserves_missing_file_behavior(server: Server) -> None:
    server.responses = [(200, None), (404, None), (500, None)]
    async with ZTPClient(server.url) as client:
        assert await client.check_file_exists("platform/1.0/firmware.bin")
        assert not await client.check_file_exists("platform/1.0/missing.bin")
        with pytest.raises(ZTPClientException):
            await client.check_file_exists("platform/1.0/unavailable.bin")
        with pytest.raises(ZTPClientException, match="three"):
            await client.check_file_exists("invalid")
    assert server.requests[0]["method"] == "HEAD"
    assert server.requests[0]["path"] == "/v1/files/platform/1.0/firmware.bin"


async def test_temporal_preserves_error_details(server: Server) -> None:
    detail = "No RBAC configuration found for this workflow"
    server.responses = [(403, {"detail": detail})]

    async with TemporalClient(server.url, "example.com") as client:
        with pytest.raises(TemporalClientException) as error:
            await client.start_workflow("/plugin/start", {})

    assert "403" in str(error.value)
    assert detail in str(error.value)


async def test_errors_non_json_and_cleanup(server: Server) -> None:
    server.responses = [
        (404, {"detail": "missing"}),
        (403, "denied"),
        (400, "invalid"),
        (200, "plain text"),
    ]
    async with ConfigStoreClient(server.url, "intended", server.url) as config:
        with pytest.raises(ConfigStoreFileNotFound):
            await config.load_file("device", "missing")
        with pytest.raises(ConfigStoreException):
            await config.get_config_file("device", "forbidden")
    async with DHCPClient(server.url) as dhcp:
        with pytest.raises(DHCPClientException):
            await dhcp.get_config()
    async with TemporalClient(server.url, "example.com") as temporal:
        assert await temporal.get_workflow("wf") == "plain text"
        with pytest.raises(TemporalClientException):
            await temporal.start_workflow("/../escape", {})
    with pytest.raises(RuntimeError, match="closed"):
        await config.whoami()
