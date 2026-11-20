# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only roles must not reach mutating API handlers."""

from configparser import ConfigParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nv_config_manager.common import auth


@pytest.fixture
def reader_api(monkeypatch):
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
    called = []

    @app.api_route(
        "/resource", methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    )
    @app.post("/healthcheck")
    def resource():
        called.append(True)
        return {"ok": True}

    auth.install_identity_probe(app)
    return TestClient(app), called


def headers(groups="api-reader"):
    return {"X-Auth-Request-Email": "reader@example.com", "X-Auth-Request-Groups": groups}


def test_read_only_groups_parsed(monkeypatch):
    cfg = ConfigParser()
    cfg.read_dict({"auth": {"read_only_groups": "api-reader, audit-reader, "}})
    monkeypatch.setattr(auth, "_auth_config", None)
    monkeypatch.setattr(auth, "_auth_config_source", None)
    assert auth.load_auth_config(cfg).read_only_groups == ("api-reader", "audit-reader")


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reader_can_read(reader_api, method):
    client, called = reader_api
    assert client.request(method, "/resource", headers=headers()).status_code == 200
    assert called == [True]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("groups", ["api-reader", "api-reader,administrator"])
def test_reader_cannot_mutate_even_with_other_roles(reader_api, method, groups):
    client, called = reader_api
    response = client.request(method, "/resource", headers=headers(groups))
    assert response.status_code == 403
    assert called == []


def test_public_path_does_not_bypass_reader_restriction(reader_api):
    client, called = reader_api
    assert client.post("/healthcheck", headers=headers()).status_code == 403
    assert called == []


def test_existing_writer_is_unchanged(reader_api):
    client, called = reader_api
    assert client.post("/resource", headers=headers("administrator")).status_code == 200
    assert called == [True]


def test_read_only_role_does_not_bypass_service_admission(reader_api, monkeypatch):
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
