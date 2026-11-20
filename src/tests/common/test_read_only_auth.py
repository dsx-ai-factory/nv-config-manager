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
"""Read-only roles must not reach mutating API handlers."""

from configparser import ConfigParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nv_config_manager.common import auth


@pytest.fixture
def reader_api(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[bool]]:
    monkeypatch.setattr(
        auth,
        "_auth_config",
        auth.AuthConfig(
            required=True,
            accept_request_headers=True,
            read_only_groups=("api-reader",),
        ),
    )
    monkeypatch.setattr(auth, "_auth_config_tracks_file", False)
    app = FastAPI()
    called: list[bool] = []

    @app.api_route(
        "/resource", methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    )
    @app.post("/healthcheck")
    @app.post("/v1/codec/decode")
    @app.post("/v1/codec/encode")
    def resource() -> dict[str, bool]:
        called.append(True)
        return {"ok": True}

    auth.install_identity_probe(app)
    return TestClient(app), called


def headers(groups: str = "api-reader") -> dict[str, str]:
    return {"X-Auth-Request-Email": "reader@example.com", "X-Auth-Request-Groups": groups}


def test_read_only_groups_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = ConfigParser()
    cfg.read_dict({"auth": {"read_only_groups": "api-reader, audit-reader, "}})
    monkeypatch.setattr(auth, "_auth_config", None)
    monkeypatch.setattr(auth, "_auth_config_source", None)
    assert auth.load_auth_config(cfg).read_only_groups == ("api-reader", "audit-reader")


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reader_can_read(reader_api: tuple[TestClient, list[bool]], method: str) -> None:
    client, called = reader_api
    assert client.request(method, "/resource", headers=headers()).status_code == 200
    assert called == [True]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("groups", ["api-reader", "api-reader,administrator"])
def test_reader_cannot_mutate_even_with_other_roles(
    reader_api: tuple[TestClient, list[bool]], method: str, groups: str
) -> None:
    client, called = reader_api
    response = client.request(method, "/resource", headers=headers(groups))
    assert response.status_code == 403
    assert called == []


@pytest.mark.parametrize("path", ["/healthcheck", "/v1/codec/decode", "/v1/codec/encode"])
def test_public_operations_work_with_reader_credentials(
    reader_api: tuple[TestClient, list[bool]],
    path: str,
) -> None:
    client, called = reader_api
    assert client.post(path, headers=headers()).status_code == 200
    assert called == [True]


def test_existing_writer_is_unchanged(reader_api: tuple[TestClient, list[bool]]) -> None:
    client, called = reader_api
    assert client.post("/resource", headers=headers("administrator")).status_code == 200
    assert called == [True]


def test_default_empty_group_list_does_not_restrict_existing_group(
    reader_api: tuple[TestClient, list[bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth,
        "_auth_config",
        auth.AuthConfig(required=True, accept_request_headers=True),
    )
    client, called = reader_api
    assert client.post("/resource", headers=headers()).status_code == 200
    assert called == [True]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
def test_dedicated_reader_group_preserves_anonymous_methods(
    reader_api: tuple[TestClient, list[bool]],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    monkeypatch.setattr(
        auth, "_auth_config", auth.AuthConfig(required=False, read_only_groups=("api-reader",))
    )
    client, called = reader_api
    assert client.request(method, "/resource").status_code == 200
    assert called == [True]


def test_universal_all_role_cannot_be_configured_as_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ConfigParser()
    config.read_dict({"auth": {"read_only_groups": "all"}})
    monkeypatch.setattr(auth, "_auth_config", None)
    monkeypatch.setattr(auth, "_auth_config_source", None)
    with pytest.raises(ValueError, match="universal role 'all'"):
        auth.load_auth_config(config)


@pytest.mark.parametrize("source", ["spiffe", "mtls"])
def test_universal_role_on_service_and_device_preserves_writes(
    reader_api: tuple[TestClient, list[bool]], monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    identity = auth.SSOIdentity(
        email="service-or-device", user="service-or-device", groups={"all"}, source=source
    )
    monkeypatch.setattr(auth, "extract_identity", lambda *_args, **_kwargs: identity)
    client, called = reader_api
    assert client.post("/resource").status_code == 200
    assert called == [True]


def test_read_only_role_does_not_bypass_service_admission(
    reader_api: tuple[TestClient, list[bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth,
        "_auth_config",
        auth.AuthConfig(
            required=True,
            accept_request_headers=True,
            allowed_groups=("different-service",),
            read_only_groups=("api-reader",),
        ),
    )
    client, called = reader_api
    assert client.get("/resource", headers=headers()).status_code == 403
    assert called == []
