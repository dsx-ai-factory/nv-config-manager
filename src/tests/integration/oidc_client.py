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
"""Noninteractive bearer authentication for deployed acceptance tests."""

from __future__ import annotations

import time

import requests


class HeadlessOIDCAuth(requests.auth.AuthBase):
    """Use a supplied token or renew Keycloak client-credentials tokens as needed."""

    def __init__(
        self,
        *,
        access_token: str = "",
        token_url: str = "",
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        """Initialize either a fixed token or a renewing client-credentials identity."""
        self._access_token = access_token
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._expires_at = 0.0

    def get_access_token(self) -> str:
        """Return the supplied token or renew a cached token before it expires."""
        if not self._token_url:
            if not self._access_token:
                raise RuntimeError("Headless OIDC authentication requires an access token")
            return self._access_token
        now = time.monotonic()
        if self._access_token and now < self._expires_at:
            return self._access_token
        try:
            response = requests.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=30,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("Token endpoint rejected the request")
            payload = response.json()
            token = payload["access_token"]
            lifetime = float(payload.get("expires_in", 60))
            if not isinstance(token, str) or not token or lifetime <= 0:
                raise ValueError("Invalid token response")
        except (requests.RequestException, KeyError, TypeError, ValueError):
            # Response bodies and request exceptions can contain credentials.
            raise RuntimeError(
                "OIDC client-credentials authentication failed; check endpoint, CA trust, "
                "client credentials, and whether service-account grants are enabled"
            ) from None
        self._access_token = token
        self._expires_at = now + lifetime * 0.8
        return token

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        """Attach a current bearer token when requests prepares an API call."""
        request.headers["Authorization"] = f"Bearer {self.get_access_token()}"
        return request
