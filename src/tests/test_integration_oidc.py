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
"""Unit coverage for unattended authentication in the deployed test suite."""

from unittest.mock import MagicMock

import pytest
import requests

from tests.integration import conftest as integration_fixtures
from tests.integration.oidc_client import HeadlessOIDCAuth


def test_client_credentials_renew_before_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    post = MagicMock()
    post.return_value.status_code = 200
    post.return_value.json.side_effect = [
        {"access_token": "first", "expires_in": 100},
        {"access_token": "second", "expires_in": 100},
    ]
    now = MagicMock(return_value=10)
    monkeypatch.setattr("tests.integration.oidc_client.requests.post", post)
    monkeypatch.setattr("tests.integration.oidc_client.time.monotonic", now)
    auth = HeadlessOIDCAuth(
        token_url="https://keycloak.example/realms/nmc/protocol/openid-connect/token",
        client_id="nvcm-tests",
        client_secret="test-secret",
    )
    session = integration_fixtures._make_sso_session(auth)
    request = requests.Request("GET", "http://render-api:9000/health")
    assert session.prepare_request(request).headers["Authorization"] == "Bearer first"
    now.return_value = 50
    assert session.prepare_request(request).headers["Authorization"] == "Bearer first"
    assert post.call_count == 1
    now.return_value = 91
    assert session.prepare_request(request).headers["Authorization"] == "Bearer second"
    assert post.call_count == 2
    assert post.call_args.kwargs["data"] == {
        "grant_type": "client_credentials",
        "client_id": "nvcm-tests",
        "client_secret": "test-secret",
    }
    assert post.call_args.kwargs["allow_redirects"] is False
    assert "verify" not in post.call_args.kwargs


@pytest.mark.parametrize(
    "status,payload", [(401, {}), (302, {}), (200, {}), (200, {"access_token": ""})]
)
def test_token_failures_do_not_expose_response_or_secret(
    monkeypatch: pytest.MonkeyPatch, status: int, payload: dict
) -> None:
    post = MagicMock()
    post.return_value.status_code = status
    post.return_value.json.return_value = payload
    post.return_value.text = "confidential-response"
    monkeypatch.setattr("tests.integration.oidc_client.requests.post", post)
    auth = HeadlessOIDCAuth(
        token_url="https://keycloak.example/token", client_id="test", client_secret="test-secret"
    )
    with pytest.raises(RuntimeError, match="authentication failed") as error:
        auth.get_access_token()
    assert "test-secret" not in str(error.value)
    assert "confidential-response" not in str(error.value)


def test_preissued_token_never_discovers_oidc_or_opens_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oidc = MagicMock()
    monkeypatch.setattr(integration_fixtures, "OIDCAuth", oidc)
    monkeypatch.delenv("NVCM_OIDC_TOKEN_URL", raising=False)
    auth = integration_fixtures.oidc_auth.__wrapped__(True, "unused.example", "test-token")
    assert isinstance(auth, HeadlessOIDCAuth)
    session = integration_fixtures._make_sso_session(auth)
    request = session.prepare_request(requests.Request("GET", "http://render-api:9000/health"))
    assert request.headers["Authorization"] == "Bearer test-token"
    oidc.discover_from_gateway.assert_not_called()
    oidc.assert_not_called()
