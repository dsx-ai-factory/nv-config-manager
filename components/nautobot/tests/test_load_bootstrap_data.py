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
"""Tests for nv_config_manager_jobs.bootstrap.load_bootstrap_data module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


def _import_module():
    import nv_config_manager_jobs.bootstrap.load_bootstrap_data as mod

    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def _make_job(mod, tmp_path, deployment_type="all"):
    """Create a LoadBootstrapData instance with data_path pointing to tmp_path."""
    with patch.dict(os.environ, {"NV_CONFIG_MANAGER_DEPLOYMENT_TYPE": deployment_type}):
        job = mod.LoadBootstrapData.__new__(mod.LoadBootstrapData)
        job.data_path = tmp_path
        job.deployment_type = deployment_type
        job.logger = MagicMock()
    return job


# ---------------------------------------------------------------------------
# should_load_item
# ---------------------------------------------------------------------------


class TestShouldLoadItem:
    def test_all_deployment_type_loads_everything(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path, "all")
        assert job.should_load_item({"deployment_types": ["all"]}) is True
        assert job.should_load_item({}) is True

    def test_matching_deployment_type(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path, "superpod")
        assert job.should_load_item({"deployment_types": ["superpod", "dgxc"]}) is True

    def test_non_matching_deployment_type(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path, "azure")
        assert job.should_load_item({"deployment_types": ["superpod"]}) is False

    def test_string_deployment_types_converted(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path, "superpod")
        assert job.should_load_item({"deployment_types": "superpod"}) is True

    def test_all_in_list_always_matches(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path, "dgxc")
        assert job.should_load_item({"deployment_types": ["all", "superpod"]}) is True


# ---------------------------------------------------------------------------
# get_content_types
# ---------------------------------------------------------------------------


class TestGetContentTypes:
    def test_valid_content_types(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)

        from django.contrib.contenttypes.models import ContentType

        mock_ct = MagicMock()
        ContentType.objects.get.return_value = mock_ct

        result = job.get_content_types(["dcim.device", "dcim.interface"])
        assert len(result) == 2

    def test_invalid_content_type_skipped(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)

        from django.contrib.contenttypes.models import ContentType

        ContentType.objects.get.side_effect = ContentType.DoesNotExist()

        result = job.get_content_types(["bad.model"])
        assert result == []

    def test_malformed_string_skipped(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)

        result = job.get_content_types(["nodot"])
        assert result == []


# ---------------------------------------------------------------------------
# load_manufacturers
# ---------------------------------------------------------------------------


class TestLoadManufacturers:
    def test_loads_manufacturer(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer

        mock_mfg = MagicMock()
        Manufacturer.objects.get_or_create.return_value = (mock_mfg, True)

        data = [{"name": "NVIDIA", "description": "GPU maker"}]
        _write_yaml(tmp_path / "manufacturers.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_manufacturers()

        Manufacturer.objects.get_or_create.assert_called_once_with(
            name="NVIDIA",
            defaults={"description": "GPU maker"},
        )
        job.logger.success.assert_called()

    def test_skips_existing_manufacturer(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer

        mock_mfg = MagicMock()
        Manufacturer.objects.get_or_create.return_value = (mock_mfg, False)

        data = [{"name": "NVIDIA"}]
        _write_yaml(tmp_path / "manufacturers.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_manufacturers()

        job.logger.info.assert_called()

    def test_missing_file(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)
        job.load_manufacturers()
        job.logger.failure.assert_called()

    def test_skips_entry_without_name(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer

        data = [{"description": "no name here"}]
        _write_yaml(tmp_path / "manufacturers.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_manufacturers()

        Manufacturer.objects.get_or_create.assert_not_called()

    def test_deployment_type_filtering(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer

        data = [{"name": "SuperPodOnly", "deployment_types": ["superpod"]}]
        _write_yaml(tmp_path / "manufacturers.yaml", data)

        job = _make_job(mod, tmp_path, "azure")
        job.load_manufacturers()

        Manufacturer.objects.get_or_create.assert_not_called()


# ---------------------------------------------------------------------------
# load_device_types (refactored)
# ---------------------------------------------------------------------------


class TestLoadDeviceTypes:
    def test_missing_directory(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)
        job.load_device_types()
        job.logger.failure.assert_called()

    def test_loads_device_type(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import DeviceType, Manufacturer

        mock_mfg = MagicMock()
        mock_mfg.name = "NVIDIA"
        Manufacturer.objects.get.return_value = mock_mfg

        mock_dt = MagicMock()
        DeviceType.objects.get_or_create.return_value = (mock_dt, True)

        mfg_dir = tmp_path / "device_types" / "NVIDIA"
        dt_data = {"model": "DGX-H100", "part_number": "ABC", "u_height": 8}
        _write_yaml(mfg_dir / "dgx-h100.yaml", dt_data)

        job = _make_job(mod, tmp_path)
        job.load_device_types()

        DeviceType.objects.get_or_create.assert_called_once_with(
            manufacturer=mock_mfg,
            model="DGX-H100",
            defaults={
                "part_number": "ABC",
                "u_height": 8,
                "is_full_depth": True,
            },
        )
        job.logger.success.assert_called()

    def test_skips_unknown_manufacturer(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import DeviceType, Manufacturer

        Manufacturer.objects.get.side_effect = Manufacturer.DoesNotExist()

        mfg_dir = tmp_path / "device_types" / "Unknown"
        _write_yaml(mfg_dir / "model.yaml", {"model": "X"})

        job = _make_job(mod, tmp_path)
        job.load_device_types()

        DeviceType.objects.get_or_create.assert_not_called()
        job.logger.warning.assert_called()

    def test_skips_invalid_yaml(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import DeviceType, Manufacturer

        mock_mfg = MagicMock()
        mock_mfg.name = "NVIDIA"
        Manufacturer.objects.get.return_value = mock_mfg

        mfg_dir = tmp_path / "device_types" / "NVIDIA"
        _write_yaml(mfg_dir / "bad.yaml", {"no_model_key": True})

        job = _make_job(mod, tmp_path)
        job.load_device_types()

        DeviceType.objects.get_or_create.assert_not_called()
        job.logger.warning.assert_called()

    def test_deployment_type_filtering(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import DeviceType, Manufacturer

        mock_mfg = MagicMock()
        mock_mfg.name = "NVIDIA"
        Manufacturer.objects.get.return_value = mock_mfg

        mfg_dir = tmp_path / "device_types" / "NVIDIA"
        dt_data = {"model": "DGX-A100", "deployment_types": ["superpod"]}
        _write_yaml(mfg_dir / "dgx-a100.yaml", dt_data)

        job = _make_job(mod, tmp_path, "azure")
        job.load_device_types()

        DeviceType.objects.get_or_create.assert_not_called()

    def test_existing_device_type(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import DeviceType, Manufacturer

        mock_mfg = MagicMock()
        mock_mfg.name = "NVIDIA"
        Manufacturer.objects.get.return_value = mock_mfg

        mock_dt = MagicMock()
        DeviceType.objects.get_or_create.return_value = (mock_dt, False)

        mfg_dir = tmp_path / "device_types" / "NVIDIA"
        _write_yaml(mfg_dir / "dgx.yaml", {"model": "DGX"})

        job = _make_job(mod, tmp_path)
        job.load_device_types()

        job.logger.success.assert_not_called()

    def test_skips_non_directory_entries(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer

        dt_dir = tmp_path / "device_types"
        dt_dir.mkdir(parents=True)
        (dt_dir / "README.md").write_text("not a directory")

        job = _make_job(mod, tmp_path)
        job.load_device_types()

        Manufacturer.objects.get.assert_not_called()

    def test_handles_exception_in_single_file(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import DeviceType, Manufacturer

        mock_mfg = MagicMock()
        mock_mfg.name = "NVIDIA"
        Manufacturer.objects.get.return_value = mock_mfg

        DeviceType.objects.get_or_create.side_effect = Exception("DB error")

        mfg_dir = tmp_path / "device_types" / "NVIDIA"
        _write_yaml(mfg_dir / "dgx.yaml", {"model": "DGX"})

        job = _make_job(mod, tmp_path)
        job.load_device_types()

        job.logger.error.assert_called()


# ---------------------------------------------------------------------------
# load_roles
# ---------------------------------------------------------------------------


class TestLoadRoles:
    def test_loads_role_with_content_types(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Role

        mock_role = MagicMock()
        Role.objects.get_or_create.return_value = (mock_role, True)
        ContentType.objects.get.return_value = MagicMock()

        data = [
            {
                "name": "Spine",
                "color": "blue",
                "weight": 100,
                "content_types": ["dcim.device"],
            }
        ]
        _write_yaml(tmp_path / "roles.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_roles()

        Role.objects.get_or_create.assert_called_once()
        mock_role.content_types.add.assert_called_once()
        mock_role.content_types.set.assert_not_called()
        job.logger.success.assert_called()

    def test_missing_file(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)
        job.load_roles()
        job.logger.failure.assert_called()


# ---------------------------------------------------------------------------
# load_tags
# ---------------------------------------------------------------------------


class TestLoadTags:
    def test_creates_tag(self, tmp_path):
        mod = _import_module()
        from nautobot.extras.models import Tag

        mock_tag = MagicMock()
        Tag.objects.update_or_create.return_value = (mock_tag, True)

        data = [
            {"name": "nv-config-manager-managed", "color": "00ff00", "description": "Managed by NVIDIA Config Manager"}
        ]
        _write_yaml(tmp_path / "tags.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_tags()

        Tag.objects.update_or_create.assert_called_once()
        job.logger.success.assert_called()

    def test_adds_tag_content_types_without_replacing_existing(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Tag

        mock_tag = MagicMock()
        mock_ct = MagicMock()
        Tag.objects.update_or_create.return_value = (mock_tag, False)
        ContentType.objects.get.return_value = mock_ct

        data = [{"name": "dhcp-subnet", "content_types": ["ipam.prefix"]}]
        _write_yaml(tmp_path / "tags.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_tags()

        mock_tag.content_types.add.assert_called_once_with(mock_ct)
        mock_tag.content_types.set.assert_not_called()


# ---------------------------------------------------------------------------
# load_custom_fields
# ---------------------------------------------------------------------------


class TestLoadCustomFields:
    def test_creates_custom_field(self, tmp_path):
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import CustomField

        mod = _import_module()

        mock_cf = MagicMock()
        mock_ct = MagicMock()
        CustomField.objects.update_or_create.return_value = (mock_cf, True)
        ContentType.objects.get.return_value = mock_ct

        data = [
            {
                "key": "nico_info",
                "label": "NICo Info",
                "type": "json",
                "description": "NICo machine info.",
                "content_types": ["dcim.device"],
            }
        ]
        _write_yaml(tmp_path / "custom_fields.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_custom_fields()

        CustomField.objects.update_or_create.assert_called_once_with(
            key="nico_info",
            defaults={
                "label": "NICo Info",
                "type": "json",
                "description": "NICo machine info.",
            },
        )
        mock_cf.content_types.add.assert_called_once_with(mock_ct)
        job.logger.success.assert_called()

    def test_filter_logic_passed_when_set(self, tmp_path):
        from nautobot.extras.models import CustomField

        mod = _import_module()

        CustomField.objects.update_or_create.return_value = (MagicMock(), True)

        data = [{"key": "nico_machine_id", "label": "NICo Machine ID", "type": "text", "filter_logic": "exact"}]
        _write_yaml(tmp_path / "custom_fields.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_custom_fields()

        _, kwargs = CustomField.objects.update_or_create.call_args
        assert kwargs["defaults"]["filter_logic"] == "exact"

    def test_missing_file(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)
        job.load_custom_fields()
        job.logger.failure.assert_called()

    def test_missing_key_logs_failure_and_skips(self, tmp_path):
        mod = _import_module()

        _write_yaml(tmp_path / "custom_fields.yaml", [{"label": "no key here"}])

        job = _make_job(mod, tmp_path)
        job.load_custom_fields()

        job.logger.failure.assert_called_once()
        assert "key" in job.logger.failure.call_args[0][0]
        from nautobot.extras.models import CustomField

        CustomField.objects.update_or_create.assert_not_called()


# ---------------------------------------------------------------------------
# load_platforms
# ---------------------------------------------------------------------------


class TestLoadPlatforms:
    def test_bundled_platforms_include_juniper_junos(self):
        mod = _import_module()
        data_path = Path(mod.__file__).resolve().parents[1] / "data" / "platforms.yaml"

        platforms = yaml.safe_load(data_path.read_text())

        assert {
            "name": "Juniper Junos",
            "manufacturer": "Juniper",
            "description": "Juniper Networks Junos OS",
            "napalm_driver": "junos",
        } in platforms

    def test_creates_platform_with_manufacturer(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer, Platform

        mock_mfg = MagicMock()
        Manufacturer.objects.get.return_value = mock_mfg
        mock_platform = MagicMock()
        Platform.objects.get_or_create.return_value = (mock_platform, True)

        data = [{"name": "Cumulus Linux", "manufacturer": "NVIDIA"}]
        _write_yaml(tmp_path / "platforms.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_platforms()

        Platform.objects.get_or_create.assert_called_once()
        job.logger.success.assert_called()

    def test_missing_manufacturer_still_creates(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Manufacturer, Platform

        Manufacturer.objects.get.side_effect = Manufacturer.DoesNotExist()
        mock_platform = MagicMock()
        Platform.objects.get_or_create.return_value = (mock_platform, True)

        data = [{"name": "Custom OS", "manufacturer": "Missing"}]
        _write_yaml(tmp_path / "platforms.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_platforms()

        call_kwargs = Platform.objects.get_or_create.call_args
        assert call_kwargs[1]["defaults"]["manufacturer"] is None


# ---------------------------------------------------------------------------
# load_tenants
# ---------------------------------------------------------------------------


class TestLoadTenants:
    def test_creates_tenant(self, tmp_path):
        mod = _import_module()
        from nautobot.tenancy.models import Tenant

        mock_tenant = MagicMock()
        Tenant.objects.get_or_create.return_value = (mock_tenant, True)

        data = [{"name": "NVIDIA", "description": "Default tenant"}]
        _write_yaml(tmp_path / "tenants.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_tenants()

        Tenant.objects.get_or_create.assert_called_once()
        job.logger.success.assert_called()


# ---------------------------------------------------------------------------
# load_namespaces
# ---------------------------------------------------------------------------


class TestLoadNamespaces:
    def test_creates_namespace(self, tmp_path):
        mod = _import_module()
        from nautobot.ipam.models import Namespace

        mock_ns = MagicMock()
        Namespace.objects.get_or_create.return_value = (mock_ns, True)

        data = [{"name": "Global", "description": "Default namespace"}]
        _write_yaml(tmp_path / "namespaces.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_namespaces()

        Namespace.objects.get_or_create.assert_called_once()


# ---------------------------------------------------------------------------
# load_statuses
# ---------------------------------------------------------------------------


class TestLoadStatuses:
    def test_creates_status(self, tmp_path):
        mod = _import_module()
        from nautobot.extras.models import Status

        mock_status = MagicMock()
        Status.objects.update_or_create.return_value = (mock_status, True)

        data = [{"name": "Provisioning", "color": "ff9800"}]
        _write_yaml(tmp_path / "statuses.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_statuses()

        Status.objects.update_or_create.assert_called_once()

    def test_adds_status_content_types_without_replacing_existing(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Status

        mock_status = MagicMock()
        mock_ct = MagicMock()
        Status.objects.update_or_create.return_value = (mock_status, False)
        ContentType.objects.get.return_value = mock_ct

        data = [{"name": "Active", "content_types": ["ipam.prefix"]}]
        _write_yaml(tmp_path / "statuses.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_statuses()

        mock_status.content_types.add.assert_called_once_with(mock_ct)
        mock_status.content_types.set.assert_not_called()


# ---------------------------------------------------------------------------
# load_location_types
# ---------------------------------------------------------------------------


class TestLoadLocationTypes:
    def test_adds_location_type_content_types_without_replacing_existing(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.dcim.models import LocationType

        mock_location_type = MagicMock()
        mock_ct = MagicMock()
        LocationType.objects.get_or_create.return_value = (mock_location_type, False)
        ContentType.objects.get.return_value = mock_ct

        data = [{"name": "Site", "content_types": ["dcim.device"]}]
        _write_yaml(tmp_path / "location_types.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_location_types()

        mock_location_type.content_types.add.assert_called_once_with(mock_ct)
        mock_location_type.content_types.set.assert_not_called()


# ---------------------------------------------------------------------------
# load_config_context_schemas
# ---------------------------------------------------------------------------


class TestLoadConfigContextSchemas:
    def test_creates_schema(self, tmp_path):
        mod = _import_module()
        from nautobot.extras.models import ConfigContextSchema

        mock_schema = MagicMock()
        ConfigContextSchema.objects.update_or_create.return_value = (mock_schema, True)

        data = [{"name": "BGP Schema", "data_schema": {"type": "object"}}]
        _write_yaml(tmp_path / "config_context_schemas.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_config_context_schemas()

        ConfigContextSchema.objects.update_or_create.assert_called_once()
        mock_schema.validated_save.assert_called_once()


# ---------------------------------------------------------------------------
# load_config_contexts
# ---------------------------------------------------------------------------


class TestLoadConfigContexts:
    def test_bootstrap_config_contexts_do_not_set_intended_firmware(self):
        data_path = Path(__file__).resolve().parents[1] / "nv_config_manager_jobs/data/config_contexts.yaml"

        with data_path.open() as f:
            config_contexts = yaml.safe_load(f)

        firmware_contexts = [
            config_context.get("name")
            for config_context in config_contexts
            if "intended-firmware" in config_context.get("data", {})
        ]

        assert firmware_contexts == []

    def test_bundled_dhcp_option_defs_include_junos_option_43(self):
        data_path = Path(__file__).resolve().parents[1] / "nv_config_manager_jobs/data/config_contexts.yaml"
        with data_path.open() as f:
            config_contexts = yaml.safe_load(f)

        option_context = next(
            context for context in config_contexts if context["name"] == "NVIDIA Config Manager DHCP Custom Options"
        )
        option_names = {entry["name"] for entry in option_context["data"]["Dhcp4"]["option-def"]}
        assert "cumulus-provision-url" in option_names
        assert {
            "image-file-name",
            "config-file-name",
            "image-file-type",
            "transfer-mode",
            "alt-image-file-name",
            "http-port",
        } <= option_names

    def test_bundled_juniper_ztp_context_targets_junos_and_full_config(self):
        data_path = Path(__file__).resolve().parents[1] / "nv_config_manager_jobs/data/config_contexts.yaml"
        with data_path.open() as f:
            config_contexts = yaml.safe_load(f)

        ztp_context = next(
            context for context in config_contexts if context["name"] == "Juniper Junos ZTP DHCP Options"
        )
        assert ztp_context["platforms"] == ["Juniper Junos"]
        reservation = ztp_context["data"]["dhcp"]["options"]["interface_names"]["fxp0"]["reservation_options"]
        assert reservation["transfer-mode"] == "http"
        assert "/firmware" in reservation["image-file-name"]
        assert "/config/full-config" in reservation["config-file-name"]
        assert set(ztp_context["data"]["dhcp"]["options"]["interface_names"]) == {
            "fxp0",
            "em0",
            "vme",
            "re0:mgmt-0",
        }

    def test_creates_config_context_with_roles_and_platforms(self, tmp_path):
        mod = _import_module()
        from nautobot.dcim.models import Platform
        from nautobot.extras.models import ConfigContext, ConfigContextSchema, Role

        mock_cc = MagicMock()
        ConfigContext.objects.update_or_create.return_value = (mock_cc, True)

        mock_role = MagicMock()
        Role.objects.get.return_value = mock_role

        mock_platform = MagicMock()
        Platform.objects.get.return_value = mock_platform

        mock_schema = MagicMock()
        ConfigContextSchema.objects.get.return_value = mock_schema

        data = [
            {
                "name": "BGP Config",
                "weight": 500,
                "data": {"asn": 65000},
                "roles": ["Spine"],
                "platforms": ["Cumulus Linux"],
                "schema": "BGP Schema",
            }
        ]
        _write_yaml(tmp_path / "config_contexts.yaml", data)

        job = _make_job(mod, tmp_path)
        job.load_config_contexts()

        ConfigContext.objects.update_or_create.assert_called_once()
        mock_cc.roles.set.assert_called_once()
        mock_cc.platforms.set.assert_called_once()
        mock_cc.validated_save.assert_called_once()


# ---------------------------------------------------------------------------
# load_relationships
# ---------------------------------------------------------------------------


class TestLoadRelationships:
    def _rel_yaml(self, **overrides):
        base = {
            "name": "prefix-to-gateway",
            "label": "prefix-to-gateway",
            "description": "Prefix to Gateway",
            "type": "one-to-one",
            "source_type": "ipam.prefix",
            "source_label": "Gateway",
            "destination_type": "ipam.ipaddress",
            "destination_label": "Gateway for Prefix",
            "required_on": "",
            "deployment_types": ["all"],
        }
        base.update(overrides)
        return base

    def test_creates_relationship_using_explicit_key(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Relationship

        mock_rel = MagicMock()
        Relationship.objects.update_or_create.return_value = (mock_rel, True)
        mock_ct = MagicMock()
        ContentType.objects.get.return_value = mock_ct

        _write_yaml(
            tmp_path / "relationships.yaml",
            [self._rel_yaml(key="prefix_to_gateway")],
        )

        job = _make_job(mod, tmp_path)
        job.load_relationships()

        Relationship.objects.update_or_create.assert_called_once_with(
            key="prefix_to_gateway",
            defaults={
                "label": "prefix-to-gateway",
                "description": "Prefix to Gateway",
                "type": "one-to-one",
                "source_type": mock_ct,
                "source_label": "Gateway",
                "destination_type": mock_ct,
                "destination_label": "Gateway for Prefix",
                "required_on": "",
            },
        )
        job.logger.success.assert_called()

    def test_explicit_key_takes_precedence_over_derived(self, tmp_path):
        """An explicit key in the YAML should be used as-is, not derived."""
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Relationship

        mock_rel = MagicMock()
        Relationship.objects.update_or_create.return_value = (mock_rel, True)
        ContentType.objects.get.return_value = MagicMock()

        data = self._rel_yaml(label="my-custom-label", key="custom_override")
        _write_yaml(tmp_path / "relationships.yaml", [data])

        job = _make_job(mod, tmp_path)
        job.load_relationships()

        call_kwargs = Relationship.objects.update_or_create.call_args
        assert call_kwargs[1]["key"] == "custom_override"
        assert call_kwargs[1]["defaults"]["label"] == "my-custom-label"

    def test_updates_existing_relationship_with_changed_label(self, tmp_path):
        """Simulate the scenario where another job changed the label on an
        existing relationship.  The bootstrap should find it by key and update
        rather than creating a duplicate with a '-2' suffix."""
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Relationship

        mock_rel = MagicMock()
        Relationship.objects.update_or_create.return_value = (mock_rel, False)
        ContentType.objects.get.return_value = MagicMock()

        data = self._rel_yaml(
            name="vlan-to-helper-address",
            key="vlan_to_helper_address",
            label="vlan-to-helper-address",
        )
        _write_yaml(tmp_path / "relationships.yaml", [data])

        job = _make_job(mod, tmp_path)
        job.load_relationships()

        call_kwargs = Relationship.objects.update_or_create.call_args
        assert call_kwargs[1]["key"] == "vlan_to_helper_address"
        assert call_kwargs[1]["defaults"]["label"] == "vlan-to-helper-address"
        job.logger.info.assert_called()

    def test_missing_file(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)
        job.load_relationships()
        job.logger.warning.assert_called()

    def test_raises_on_missing_required_field(self, tmp_path):
        mod = _import_module()

        _write_yaml(tmp_path / "relationships.yaml", [{"label": "orphan"}])

        job = _make_job(mod, tmp_path)
        with pytest.raises(KeyError):
            job.load_relationships()

    def test_raises_on_unresolvable_content_type(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType

        ContentType.objects.get.side_effect = ContentType.DoesNotExist()

        _write_yaml(tmp_path / "relationships.yaml", [self._rel_yaml()])

        job = _make_job(mod, tmp_path)
        with pytest.raises(ContentType.DoesNotExist):
            job.load_relationships()

    def test_deployment_type_filtering(self, tmp_path):
        mod = _import_module()
        from nautobot.extras.models import Relationship

        data = self._rel_yaml(deployment_types=["superpod"])
        _write_yaml(tmp_path / "relationships.yaml", [data])

        job = _make_job(mod, tmp_path, "azure")
        job.load_relationships()

        Relationship.objects.update_or_create.assert_not_called()

    def test_label_with_special_chars_derives_correct_key(self, tmp_path):
        mod = _import_module()
        from django.contrib.contenttypes.models import ContentType
        from nautobot.extras.models import Relationship

        mock_rel = MagicMock()
        Relationship.objects.update_or_create.return_value = (mock_rel, True)
        ContentType.objects.get.return_value = MagicMock()

        data = self._rel_yaml(label="my-cool relationship!")
        _write_yaml(tmp_path / "relationships.yaml", [data])

        job = _make_job(mod, tmp_path)
        job.load_relationships()

        call_kwargs = Relationship.objects.update_or_create.call_args
        assert call_kwargs[1]["key"] == "my_cool_relationship"


# ---------------------------------------------------------------------------
# run (integration-style)
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_calls_all_loaders_in_order(self, tmp_path):
        mod = _import_module()
        job = _make_job(mod, tmp_path)

        expected_methods = [
            "load_manufacturers",
            "load_tenants",
            "load_location_types",
            "load_namespaces",
            "load_statuses",
            "load_roles",
            "load_tags",
            "load_platforms",
            "load_device_types",
            "load_relationships",
            "load_config_context_schemas",
            "load_config_contexts",
        ]

        call_order = []
        for method_name in expected_methods:
            mock = MagicMock(side_effect=lambda name=method_name: call_order.append(name))
            setattr(job, method_name, mock)

        job.run()

        assert call_order == expected_methods
