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
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

from aioresponses import aioresponses
from fastapi.testclient import TestClient

from nv_config_manager.temporal.api.main import app

V2_SITES = {
    "data": {"locations": [{"id": "ddadde54-cbdd-4fa5-94ce-ca649b7e2aa8", "name": "SITEA"}]}
}

DEVICES = {
    "data": {
        "devices": [
            {
                "id": "aa6ef75b-00fe-45e6-8adb-62609509cb4f",
                "name": "core1-cg1-cp1-tan1-sitea",
                "platform": {"name": "Cumulus Linux"},
            }
        ]
    }
}

DEVICE_INTERFACES = {
    "data": {
        "interfaces": [
            {
                "id": "interface-2",
                "name": "swp2",
                "device": {"id": "device-1", "name": "leaf-1"},
            },
            {
                "id": "interface-1",
                "name": "swp1",
                "device": {"id": "device-1", "name": "leaf-1"},
            },
        ]
    }
}

# config_manager_devices response for tenant endpoint (managed_only)
NV_CONFIG_MANAGER_DEVICES_TENANTS = {
    "data": {
        "config_manager_devices": [
            {"device": {"tenant": {"id": "tenant-uuid-1", "name": "TenantA"}}},
            {"device": {"tenant": {"id": "tenant-uuid-2", "name": "TenantB"}}},
            {"device": {"tenant": {"id": "tenant-uuid-3", "name": "Example Cloud"}}},
        ]
    }
}

# config_manager_devices response for role endpoint (managed_only)
NV_CONFIG_MANAGER_DEVICES_ROLES = {
    "data": {
        "config_manager_devices": [
            {"device": {"role": {"id": "role-uuid-1", "name": "leaf"}}},
            {"device": {"role": {"id": "role-uuid-2", "name": "spine"}}},
            {"device": {"role": {"id": "role-uuid-1", "name": "leaf"}}},
        ]
    }
}

# All tenants (default)
TENANTS = {
    "data": {
        "tenants": [
            {"id": "tenant-uuid-1", "name": "TenantA"},
            {"id": "tenant-uuid-2", "name": "TenantB"},
            {"id": "tenant-uuid-3", "name": "Example Cloud"},
        ]
    }
}

# All roles (default)
ROLES = {
    "data": {
        "roles": [
            {"id": "role-uuid-1", "name": "leaf"},
            {"id": "role-uuid-2", "name": "spine"},
        ]
    }
}

STATUSES = {
    "data": {
        "statuses": [
            {"id": "status-uuid-1", "name": "Active"},
            {"id": "status-uuid-2", "name": "Provisioned"},
            {"id": "status-uuid-3", "name": "Decommissioned"},
        ]
    }
}

NAMESPACE_TAGS = {
    "data": {
        "namespaces": [
            {"tags": [{"name": "spectrumx"}, {"name": "tenant-a"}]},
            {"tags": [{"name": "spectrumx"}]},
            {"tags": []},
        ]
    }
}

SPX_OVERLAYS = {
    "count": 2,
    "next": None,
    "previous": None,
    "results": [
        {"id": "overlay-uuid-2", "name": "overlay-b"},
        {"id": "overlay-uuid-1", "name": "overlay-a"},
    ],
}


def test_device_secrets_accepts_mapping() -> None:
    """Provider implementations may return any read-only Mapping."""
    dcim_client = MagicMock()
    dcim_client.__aenter__ = AsyncMock(return_value=dcim_client)
    dcim_client.__aexit__ = AsyncMock(return_value=None)
    dcim_client.get_device_secret_versions = AsyncMock(
        return_value=MappingProxyType({"tacacs_key": "r1"})
    )

    with patch(
        "nv_config_manager.temporal.api.parameter_v1.create_dcim_client",
        return_value=dcim_client,
    ):
        response = TestClient(app).get("/v1/parameter/device/device-1/secrets")

    assert response.status_code == 200
    assert response.json() == [{"name": "tacacs_key_r1", "description": "tacacs_key version r1"}]


def test_site_v2():
    with aioresponses() as m:
        # Mock the graphql endpoint to return V2_SITES data
        # URL comes from conftest.py mock config
        m.post("https://nautobot.example.com/api/graphql/", payload=V2_SITES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/site")
        assert rsp.json() == [{"id": "ddadde54-cbdd-4fa5-94ce-ca649b7e2aa8", "name": "SITEA"}]

    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=V2_SITES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/site?location_type=Site")
        assert rsp.json() == [{"id": "ddadde54-cbdd-4fa5-94ce-ca649b7e2aa8", "name": "SITEA"}]


def test_device_v2():
    with aioresponses() as m:
        # Mock the graphql endpoint to return DEVICES data for all calls
        # URL comes from conftest.py mock config
        m.post("https://nautobot.example.com/api/graphql/", payload=DEVICES, repeat=True)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device?site=SITEA&status=Active&tenant=TenantA")
        assert rsp.json() == [
            {
                "id": "aa6ef75b-00fe-45e6-8adb-62609509cb4f",
                "name": "core1-cg1-cp1-tan1-sitea",
                "platform": "cumulus-linux",
            }
        ]

        # Test with custom platform
        rsp = client.get(
            "/v1/parameter/device?site=SITEA&status=Active&tenant=TenantA&platform=UFM"
        )
        assert rsp.json() == [
            {
                "id": "aa6ef75b-00fe-45e6-8adb-62609509cb4f",
                "name": "core1-cg1-cp1-tan1-sitea",
                "platform": "cumulus-linux",
            }
        ]


def test_device_interfaces():
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=DEVICE_INTERFACES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device/aa6ef75b-00fe-45e6-8adb-62609509cb4f/interfaces")

        assert rsp.json() == [
            {"id": "interface-1", "name": "swp1"},
            {"id": "interface-2", "name": "swp2"},
        ]
        sent = next(iter(m.requests.values()))[0]
        assert sent.kwargs["json"]["variables"] == {
            "device_id": ["aa6ef75b-00fe-45e6-8adb-62609509cb4f"]
        }


UFM_DEVICES = {
    "data": {
        "devices": [
            {
                "id": "ufm-uuid-1",
                "name": "ufm-test-device",
                "platform": {"name": "UFM"},
            }
        ]
    }
}

# Server-side managed_only filter returns only the managed switch.
MANAGED_DEVICES = {
    "data": {
        "devices": [
            {
                "id": "aa6ef75b-00fe-45e6-8adb-62609509cb4f",
                "name": "managed-switch",
                "platform": {"name": "Cumulus Linux"},
            },
        ]
    }
}


def test_device_no_platform_allow_list():
    """Without an explicit platform, no default platform allow-list is injected."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=DEVICES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device?site=SITEA")
        assert rsp.status_code == 200

        sent = next(iter(m.requests.values()))[0]
        variables = sent.kwargs["json"]["variables"]
        assert "platform" not in variables


def test_device_role_without_platform_filter():
    """A role-scoped query is trusted as-is, with no platform allow-list injected."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=UFM_DEVICES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device?role=UFM")
        assert rsp.json() == [{"id": "ufm-uuid-1", "name": "ufm-test-device", "platform": "ufm"}]

        sent = next(iter(m.requests.values()))[0]
        variables = sent.kwargs["json"]["variables"]
        assert variables["role"] == ["UFM"]
        assert "platform" not in variables


def test_device_managed_only():
    """managed_only=true sets the nv_config_manager_device_status filter in one query."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=MANAGED_DEVICES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device?site=SITEA&managed_only=true")
        assert rsp.json() == [
            {
                "id": "aa6ef75b-00fe-45e6-8adb-62609509cb4f",
                "name": "managed-switch",
                "platform": "cumulus-linux",
            }
        ]

        # Single round-trip; the managed filter is passed as a GraphQL variable.
        assert len(m.requests) == 1
        sent = next(iter(m.requests.values()))[0]
        assert sent.kwargs["json"]["variables"]["managed_only"] is True


def test_device_managed_only_omitted_by_default():
    """Without managed_only, the filter variable is omitted (null = no constraint)."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=DEVICES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device?site=SITEA")
        assert rsp.status_code == 200

        sent = next(iter(m.requests.values()))[0]
        assert "managed_only" not in sent.kwargs["json"]["variables"]


def test_device_graphql_error_returns_400():
    """A GraphQL error is translated to HTTP 400 instead of an unhandled 500."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"errors": [{"message": "invalid query"}]},
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/device?site=SITEA")
        assert rsp.status_code == 400


def test_tenant_default():
    """Test the tenant parameter endpoint (default: all tenants)."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=TENANTS)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/tenant")
        assert rsp.json() == [
            {"id": "tenant-uuid-1", "name": "TenantA"},
            {"id": "tenant-uuid-2", "name": "TenantB"},
            {"id": "tenant-uuid-3", "name": "Example Cloud"},
        ]


def test_tenant_managed_only():
    """Test the tenant parameter endpoint (managed_only: from nv_config_manager_devices)."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=NV_CONFIG_MANAGER_DEVICES_TENANTS,
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/tenant?managed_only=true")
        result = rsp.json()
        assert len(result) == 3
        names = {t["name"] for t in result}
        assert names == {"TenantA", "TenantB", "Example Cloud"}


def test_role_default():
    """Test the role parameter endpoint (default: all roles)."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=ROLES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/role")
        assert rsp.json() == [
            {"id": "role-uuid-1", "name": "leaf"},
            {"id": "role-uuid-2", "name": "spine"},
        ]


def test_role_managed_only():
    """Test the role parameter endpoint (managed_only: from nv_config_manager_devices)."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload=NV_CONFIG_MANAGER_DEVICES_ROLES,
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/role?managed_only=true")
        result = rsp.json()
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"leaf", "spine"}


def test_namespace_tag():
    """Test the namespace tag parameter endpoint."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=NAMESPACE_TAGS)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/namespace-tag?location=RNO1")
        assert rsp.json() == [
            {"id": "spectrumx", "name": "spectrumx"},
            {"id": "tenant-a", "name": "tenant-a"},
        ]


def test_namespace_tag_graphql_error():
    """Test the namespace tag endpoint handles provider query errors."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"errors": [{"message": "boom"}]},
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/namespace-tag")
        assert rsp.status_code == 500
        assert rsp.json() == {"detail": "Failed to query DCIM namespace tags."}


def test_namespace_tag_malformed_response():
    """Test the namespace tag endpoint handles malformed provider responses."""
    with aioresponses() as m:
        m.post(
            "https://nautobot.example.com/api/graphql/",
            payload={"data": {"namespaces": {}}},
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/namespace-tag")
        assert rsp.status_code == 500
        assert rsp.json() == {"detail": "Malformed DCIM namespace tag response."}


def test_overlays_with_filters():
    """Test the generic overlay parameter endpoint with filters."""
    with aioresponses() as m:
        m.get(
            "https://nautobot.example.com/api/plugins/overlays/overlays/"
            "?location=SITEA&isolation_type=spectrum_x_vrf&limit=250&offset=0",
            payload=SPX_OVERLAYS,
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/overlay?location=SITEA&isolation_type=spectrum_x_vrf")
        assert rsp.json() == [
            {"id": "overlay-uuid-1", "name": "overlay-a"},
            {"id": "overlay-uuid-2", "name": "overlay-b"},
        ]


def test_overlay_query_failure_is_logged():
    """Log the underlying provider failure while preserving the generic API response."""
    with (
        aioresponses() as m,
        patch("nv_config_manager.temporal.api.parameter_v1.logger.exception") as log_exception,
    ):
        m.get(
            "https://nautobot.example.com/api/plugins/overlays/overlays/?limit=250&offset=0",
            status=500,
        )

        client = TestClient(app)
        rsp = client.get("/v1/parameter/overlay")

    assert rsp.status_code == 500
    assert rsp.json() == {"detail": "Failed to query DCIM overlays."}
    log_exception.assert_called_once()
    assert isinstance(log_exception.call_args.kwargs["exc_info"], Exception)


def test_status_with_content_type():
    """Test the status parameter endpoint with content_type filter."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=STATUSES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/status?content_type=dcim.device")
        assert rsp.json() == [
            {"id": "status-uuid-1", "name": "Active"},
            {"id": "status-uuid-2", "name": "Provisioned"},
            {"id": "status-uuid-3", "name": "Decommissioned"},
        ]


def test_status_without_content_type():
    """Test the status parameter endpoint without filter (all statuses)."""
    with aioresponses() as m:
        m.post("https://nautobot.example.com/api/graphql/", payload=STATUSES)

        client = TestClient(app)
        rsp = client.get("/v1/parameter/status")
        assert rsp.json() == [
            {"id": "status-uuid-1", "name": "Active"},
            {"id": "status-uuid-2", "name": "Provisioned"},
            {"id": "status-uuid-3", "name": "Decommissioned"},
        ]
