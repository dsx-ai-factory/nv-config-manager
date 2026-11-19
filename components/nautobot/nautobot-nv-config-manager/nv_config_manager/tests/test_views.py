#  SPDX-FileCopyrightText: Copyright (c) "2025" NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#  SPDX-License-Identifier: Apache-2.0
#
#  Licensed under the Apache License, Version 2.0 (the "License")
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# pylint: disable=C0302, too-many-ancestors
"""Tests for nv_config_manager views."""

import re
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, Q
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from nautobot.apps.testing import (
    TestCase,
    ViewTestCases,
    extract_page_body,
)
from nautobot.dcim.models import (
    Device,
    DeviceType,
    Location,
    LocationType,
    Manufacturer,
    Platform,
    Rack,
)
from nautobot.extras.choices import ObjectChangeActionChoices
from nautobot.extras.models import ObjectChange, Role, Status
from nautobot.tenancy.models import Tenant
from nautobot.users.models import ObjectPermission

from nv_config_manager.models import (
    BackupConfig,
    ConfigManagerDeviceStatus,
    IntendedConfig,
)
from nv_config_manager.tests.fixtures import mock_data as data
from nv_config_manager.tests.fixtures.create_obj_fixtures import create_nested_locations

with_temporal_url = override_settings(
    PLUGINS_CONFIG={"nv_config_manager": {"temporal_url": "https://temporal.example.com"}}
)


def extract_links(html_content, link_texts):
    """
    Extracts and returns the `href` values for the given link texts in the HTML content.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    links = {}
    for row in soup.find_all("tr"):
        header = row.find("th")
        if header is None or header.get_text(strip=True) not in link_texts:
            continue
        link = row.find("a", href=True)
        if link is not None:
            links[header.get_text(strip=True)] = link["href"]
    return links


class ConfigManagerViewTestMixin:
    """Shared setup for Config Manager view tests."""

    def setUp(self):
        """Use an explicit host allowed by the test Nautobot config."""
        super().setUp()
        self.client.defaults["HTTP_HOST"] = data.TEST_HTTP_HOST


class ConfigManagerDeviceStatusTestCase(
    ConfigManagerViewTestMixin,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.GetObjectViewTestCase,
):  # pylint: disable=too-many-ancestors
    """Test cases for ConfigManagerDeviceStatus View."""

    model = ConfigManagerDeviceStatus
    # Allow recursive queries for location tree traversal
    allowed_number_of_tree_queries_per_view_type = {"retrieve": 1}

    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name=data.MANUFACTURER_NAME)
        site_type, _ = LocationType.objects.get_or_create(name="Site")
        module_type, _ = LocationType.objects.get_or_create(name="Module", parent=site_type)
        location_status = Status.objects.get_for_model(Location).first()
        device_status = Status.objects.get_for_model(Device).first()

        cls.module = Location.objects.create(name=data.MODULE_NAME, location_type=module_type, status=location_status)
        cls.site = Location.objects.create(
            name=data.SITE_NAME,
            location_type=site_type,
            parent=cls.module,
            status=location_status,
        )
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=data.DEVICE_TYPE_MODEL,
        )
        cls.device_role = Role.objects.create(
            name=data.LEAF_ROLE_NAME,
            color="ff0000",
        )
        cls.non_managed_device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.NON_MANAGED_DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )
        cls.device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )
        cls.device2 = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.FIFTH_DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )

        cls.managed_device = ConfigManagerDeviceStatus.objects.create(device=cls.device)
        cls.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_INTENDED_COMMIT_ID,
        )

        cls.managed_device_overdue = ConfigManagerDeviceStatus.objects.create(device=cls.device2)
        cls.managed_device_overdue.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device_overdue,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now() - timedelta(hours=73),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device_overdue.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device_overdue,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now() - timedelta(hours=73),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
        )

    def test_pending_deploy_urls(self):
        """Test deploy button url."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        url = reverse("plugins:nv_config_manager:configmanagerdevicestatus_list")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

        response_content = extract_page_body(response.content.decode(response.charset))
        soup = BeautifulSoup(response_content, "html.parser")
        button = soup.find("button", class_="btn btn-warning")

        link = button.find("a")
        href = link["href"]
        parsed_url = urlparse(href)
        query_params = parse_qs(parsed_url.query)

        site = query_params.get("site", [None])[0]
        device_id = query_params.get("device-id", [None])[0]

        self.assertEqual(site, self.site.name)
        self.assertEqual(device_id, str(self.managed_device_overdue.id))

    @with_temporal_url
    def test_see_diff_column_present(self):
        """The managed devices list exposes a per-row See Diff workflow link."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

        url = reverse("plugins:nv_config_manager:configmanagerdevicestatus_list")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

        response_content = extract_page_body(response.content.decode(response.charset))
        soup = BeautifulSoup(response_content, "html.parser")
        diff_links = [
            a["href"] for a in soup.find_all("a", href=True) if "/workflows/configdiffworkflow/form" in a["href"]
        ]
        self.assertTrue(diff_links, "See Diff link missing from the managed devices list.")
        expected_status = self.device.status.name
        for href in diff_links:
            query_params = parse_qs(urlparse(href).query)
            self.assertIn("device-id", query_params)
            # The device's status is carried so the workflow form pre-selects it.
            self.assertEqual(query_params.get("status", [None])[0], expected_status)

    def test_view_managed_devices_stats(self):
        """Test viewing managed device stats."""

        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

        url = reverse("plugins:nv_config_manager:configmanagerdevicestatus_list")
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        response_content = extract_page_body(response.content.decode(response.charset))

        # Regular expression pattern to match numeric values within h2 tags
        numeric_pattern = r"<h2[^>]*>\s*([0-9]+)\s*</h2>"

        # Find all matches of the pattern in the HTML content
        numeric_values = re.findall(numeric_pattern, response_content)

        # Regular expression pattern to match description text within p tags
        description_pattern = r"<p[^>]*>(.*?)</p>"

        # Find all matches of the pattern in the HTML content
        # NOTE: delete "Local" from descriptions if running locally.
        descriptions = [desc for desc in re.findall(description_pattern, response_content) if "Local" not in desc]

        if not descriptions or not numeric_values:
            self.fail("No stats found.")
        # Iterate over numeric values and descriptions
        for numeric_value, description in zip(numeric_values, descriptions, strict=False):
            print(description.strip(), numeric_value.strip())
            if description.strip() == "Total Managed Devices":
                self.assertEqual(numeric_value.strip(), "2")
            elif description.strip() == "All Pending Deployments":
                self.assertEqual(numeric_value.strip(), "1")
            else:
                self.fail(f"Unexpected description: {description}")

    def test_list_view_filter_by_is_aggregate_managed(self):
        """Test filtering ConfigManagerDeviceStatus by is_aggregate_managed field."""
        # Set up permissions
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

        # Create a device with a completely different name to avoid substring issues
        aggregate_device = Device.objects.create(
            device_type=self.device_type,
            role=self.device_role,
            name=data.AGGREGATE_DEVICE_NAME,
            location=self.site,
            status=Status.objects.get_for_model(Device).first(),
        )

        # Create an additional managed device with is_aggregate_managed=True
        managed_device_aggregate = ConfigManagerDeviceStatus.objects.create(
            device=aggregate_device, is_aggregate_managed=True
        )

        # Test filtering for is_aggregate_managed=True
        response = self.client.get(
            reverse("plugins:nv_config_manager:configmanagerdevicestatus_list") + "?is_aggregate_managed=true"
        )
        self.assertHttpStatus(response, 200)

        # Should only return the aggregate-managed device
        self.assertContains(response, managed_device_aggregate.device.name)
        self.assertNotContains(response, self.managed_device.device.name)
        self.assertNotContains(response, self.managed_device_overdue.device.name)

        # Test filtering for is_aggregate_managed=false
        response = self.client.get(
            reverse("plugins:nv_config_manager:configmanagerdevicestatus_list") + "?is_aggregate_managed=false"
        )
        self.assertHttpStatus(response, 200)

        # Should return the non-aggregate devices
        self.assertNotContains(response, managed_device_aggregate.device.name)
        self.assertContains(response, self.managed_device.device.name)
        self.assertContains(response, self.managed_device_overdue.device.name)


class TestConfigManagerDeviceStatusDetailView(
    ConfigManagerViewTestMixin,
    ViewTestCases.GetObjectViewTestCase,
):
    """Test the managed device detail view."""

    model = ConfigManagerDeviceStatus
    # Allow recursive queries for location tree traversal
    allowed_number_of_tree_queries_per_view_type = {"retrieve": 1}

    @classmethod
    def setUpTestData(cls):
        # Create mock config objects
        manufacturer = Manufacturer.objects.create(name=data.MANUFACTURER_NAME)
        site_type, _ = LocationType.objects.get_or_create(name="Site")
        module_type, _ = LocationType.objects.get_or_create(name="Module", parent=site_type)
        location_status = Status.objects.get_for_model(Location).first()
        device_status = Status.objects.get_for_model(Device).first()
        cls.site = Location.objects.create(name=data.SITE_NAME, location_type=site_type, status=location_status)
        cls.module = Location.objects.create(
            name=data.MODULE_NAME,
            location_type=module_type,
            parent=cls.site,
            status=location_status,
        )
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=data.DEVICE_TYPE_MODEL,
        )
        cls.device_role = Role.objects.create(
            name=data.LEAF_ROLE_NAME,
            color="ff0000",
        )
        cls.device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.DEVICE_NAME,
            location=cls.module,
            status=device_status,
        )
        cls.device2 = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.SECOND_DEVICE_NAME,
            location=cls.module,
            status=device_status,
        )

        cls.managed_device = ConfigManagerDeviceStatus.objects.create(device=cls.device)
        cls.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_INTENDED_COMMIT_ID,
        )

        cls.managed_device2 = ConfigManagerDeviceStatus.objects.create(device=cls.device2)
        cls.managed_device2.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device2,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_UPDATED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device2.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device2,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_UPDATED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_BACKUP_WORKFLOW_ID,
            deployed_commit_id=data.TEST_UPDATED_COMMIT_ID,
        )

    @with_temporal_url
    def test_workflow_urls(self):
        """Test the urls are correct."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        obj_perm.object_types.add(ContentType.objects.get_for_model(Device))
        url = reverse(
            "plugins:nv_config_manager:device_config_manager_workflows",
            args=[self.managed_device.pk],
        )
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

        response_content = extract_page_body(response.content.decode(response.charset))
        soup = BeautifulSoup(response_content, "html.parser")
        workflow_panel = soup.find("table")
        href_values = [a["href"] for a in workflow_panel.find_all("a", href=True)]
        self.assertEqual(len(href_values), 6)
        self.assertTrue(
            any("/workflows/configdiffworkflow/form" in href for href in href_values),
            "Config Diff workflow launch link missing from the workflows tab.",
        )

        for href in href_values:
            parsed_url = urlparse(href)
            query_params = parse_qs(parsed_url.query)

            site = query_params.get("site", [None])[0]
            device_id = query_params.get("device-id", [None])[0]

            self.assertEqual(site, self.module.name)
            self.assertEqual(device_id, str(self.managed_device.id))


class ConfigManagerDeviceTabTestCase(  # pylint: disable=too-many-ancestors
    ConfigManagerViewTestMixin,
    ViewTestCases.GetObjectViewTestCase,
):
    """Tests for the managed-device info tab in Device view."""

    model = Device
    # Allow recursive queries for location tree traversal
    allowed_number_of_tree_queries_per_view_type = {"retrieve": 1}

    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name=data.MANUFACTURER_NAME)
        site_type, _ = LocationType.objects.get_or_create(name="Site")
        location_status = Status.objects.get_for_model(Location).first()
        device_status = Status.objects.get_for_model(Device).first()
        cls.site = Location.objects.create(name=data.SITE_NAME, location_type=site_type, status=location_status)
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=data.DEVICE_TYPE_MODEL,
        )
        cls.device_role = Role.objects.create(
            name=data.LEAF_ROLE_NAME,
            color="ff0000",
        )
        cls.device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )
        cls.device2 = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.SECOND_DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )
        cls.non_managed_device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.NON_MANAGED_DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )

        cls.device3 = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.CONFIG_STORE_DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )

        # Create ConfigManagerDeviceStatus for device and device2
        cls.managed_device = ConfigManagerDeviceStatus.objects.create(device=cls.device)
        cls.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_INTENDED_COMMIT_ID,
        )

        cls.managed_device2 = ConfigManagerDeviceStatus.objects.create(device=cls.device2)
        cls.managed_device2.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device2,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_DEPLOYABLE_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device2.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device2,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_DEPLOYABLE_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_BACKUP_WORKFLOW_ID,
            deployed_commit_id=data.TEST_DEPLOYABLE_COMMIT_ID,
        )

        cls.managed_device_config_store = ConfigManagerDeviceStatus.objects.create(device=cls.device3)
        cls.managed_device_config_store.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device_config_store,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_DEPLOYABLE_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device_config_store.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device_config_store,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
        )

    def test_view_config_details(self):
        """Test viewing config details."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        url = reverse("plugins:nv_config_manager:device_config_manager_info", args=[self.device.pk])
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

    def test_config_store_url_generation(self):
        """Test config store url format."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

        url = reverse("plugins:nv_config_manager:device_config_manager_info", args=[self.device3.pk])
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)
        response_content = extract_page_body(response.content.decode(response.charset))
        links = extract_links(
            response_content,
            [
                "Intended Config Version",
                "Intended Config History",
                "Last Config Backup",
                "Backup History",
            ],
        )
        device_uuid = str(self.device3.pk)
        config_store_url = data.CONFIG_STORE_UI_URL.rstrip("/")
        self.assertEqual(
            links.get("Intended Config Version"),
            f"{config_store_url}/device/{device_uuid}/{data.CONFIG_PATH}"
            f"?file_type=intended&version={data.TEST_DEPLOYABLE_COMMIT_ID}",
        )
        self.assertEqual(
            links.get("Intended Config History"),
            f"{config_store_url}/device/{device_uuid}/{data.CONFIG_PATH}/history?file_type=intended",
        )
        self.assertEqual(
            links.get("Last Config Backup"),
            f"{config_store_url}/device/{device_uuid}/{data.BACKUP_CONFIG_PATH}"
            f"?file_type=backup&version={data.TEST_BACKUP_COMMIT_ID}",
        )
        self.assertEqual(
            links.get("Backup History"),
            f"{config_store_url}/device/{device_uuid}/{data.BACKUP_CONFIG_PATH}/history?file_type=backup",
        )

    def test_view_config_details_dne(self):
        """Test viewing config details for a non managed device."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

        url = reverse(
            "plugins:nv_config_manager:device_config_manager_info",
            args=[self.non_managed_device.pk],
        )
        response = self.client.get(url)
        self.assertHttpStatus(response, 404)


class LocationManagedDevicesTabTestCase(  # pylint: disable=too-many-ancestors
    ConfigManagerViewTestMixin,
    ViewTestCases.GetObjectViewTestCase,
):
    """Tests for the managed-devices tab in the Location view."""

    model = Location
    # Allow recursive queries for location tree traversal
    allowed_number_of_tree_queries_per_view_type = {"retrieve": 3}

    @classmethod
    def setUpTestData(cls):
        create_nested_locations()
        cls.region, _ = Location.objects.get_or_create(name=data.REGION_NAME)
        manufacturer, _ = Manufacturer.objects.get_or_create(name=data.MANUFACTURER_NAME)
        site_type, _ = LocationType.objects.get_or_create(name="Site")
        module_type, _ = LocationType.objects.get_or_create(name="Module", parent=site_type)
        location_status = Status.objects.get_for_model(Location).first()
        device_status = Status.objects.get_for_model(Device).first()

        cls.module = Location.objects.create(
            name=data.SECOND_MODULE_NAME,
            location_type=module_type,
            status=location_status,
        )
        cls.site = Location.objects.create(
            name=data.SECOND_SITE_NAME,
            location_type=site_type,
            parent=cls.module,
            status=location_status,
        )
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=data.SECOND_DEVICE_TYPE_MODEL,
        )
        cls.device_role = Role.objects.create(
            name=data.AGGREGATE_ROLE_NAME,
            color="ff0000",
        )
        cls.device2 = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.FIFTH_DEVICE_NAME,
            location=cls.site,
            status=device_status,
        )

        cls.managed_device_overdue = ConfigManagerDeviceStatus.objects.create(device=cls.device2)
        cls.managed_device_overdue.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device_overdue,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now() - timedelta(hours=73),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device_overdue.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device_overdue,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now() - timedelta(hours=73),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
        )

    def test_pending_deploy_urls(self):
        """Test deploy button url."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        url = reverse("plugins:nv_config_manager:location_managed_devices", args=[self.module.pk])
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

        response_content = extract_page_body(response.content.decode(response.charset))
        soup = BeautifulSoup(response_content, "html.parser")
        button = soup.find("button", class_="btn btn-warning")

        link = button.find("a")
        href = link["href"]
        parsed_url = urlparse(href)
        query_params = parse_qs(parsed_url.query)

        site = query_params.get("site", [None])[0]
        device_id = query_params.get("device-id", [None])[0]

        self.assertEqual(site, self.site.name)
        self.assertEqual(device_id, str(self.managed_device_overdue.id))

    def test_view_device_details(self):
        """Test viewing config details."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        url = reverse("plugins:nv_config_manager:location_managed_devices", args=[self.region.pk])
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)


class ConfigManagerWorkflowsTabTestCase(ConfigManagerViewTestMixin, ViewTestCases.GetObjectViewTestCase):
    """Tests for the config-manager workflows tab in the Device view."""

    model = Device
    # Allow recursive queries for location tree traversal
    allowed_number_of_tree_queries_per_view_type = {"retrieve": 1}

    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name=data.MANUFACTURER_NAME)
        site_type, _ = LocationType.objects.get_or_create(name="Site")
        module_type, _ = LocationType.objects.get_or_create(name="Module", parent=site_type)
        location_status = Status.objects.get_for_model(Location).first()
        cls.site = Location.objects.create(name=data.SITE_NAME, location_type=site_type, status=location_status)
        cls.module = Location.objects.create(
            name=data.MODULE_NAME,
            location_type=module_type,
            status=location_status,
            parent=cls.site,
        )
        device_status = Status.objects.get_for_model(Device).first()
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model=data.DEVICE_TYPE_MODEL,
        )
        cls.device_role = Role.objects.create(
            name=data.LEAF_ROLE_NAME,
            color="ff0000",
        )
        cls.device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.DEVICE_NAME,
            location=cls.module,
            status=device_status,
        )
        cls.non_managed_device = Device.objects.create(
            device_type=cls.device_type,
            role=cls.device_role,
            name=data.NON_MANAGED_DEVICE_NAME,
            location=cls.module,
            status=device_status,
        )

        cls.managed_device = ConfigManagerDeviceStatus.objects.create(device=cls.device)
        cls.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        cls.managed_device.backup_config = BackupConfig.objects.create(
            device_id=cls.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_INTENDED_COMMIT_ID,
        )

    @with_temporal_url
    def test_workflow_urls(self):
        """Test the urls are correct."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        url = reverse("plugins:nv_config_manager:device_config_manager_workflows", args=[self.device.pk])
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

        response_content = extract_page_body(response.content.decode(response.charset))
        soup = BeautifulSoup(response_content, "html.parser")
        workflow_panel = soup.find("table")
        href_values = [a["href"] for a in workflow_panel.find_all("a", href=True)]
        self.assertEqual(len(href_values), 6)
        self.assertTrue(
            any("/workflows/configdiffworkflow/form" in href for href in href_values),
            "Config Diff workflow launch link missing from the workflows tab.",
        )

        for href in href_values:
            parsed_url = urlparse(href)
            query_params = parse_qs(parsed_url.query)

            site = query_params.get("site", [None])[0]
            device_id = query_params.get("device-id", [None])[0]

            self.assertEqual(site, self.module.name)
            self.assertEqual(device_id, str(self.managed_device.id))

    def test_view_workflows_details(self):
        """Test viewing config details."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))
        url = reverse("plugins:nv_config_manager:device_config_manager_workflows", args=[self.device.pk])
        response = self.client.get(url)
        self.assertHttpStatus(response, 200)

    def test_view_workflows_details_dne(self):
        """Test viewing workflows for a non managed device."""
        obj_perm = ObjectPermission(name="Test permission", actions=["view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

        url = reverse(
            "plugins:nv_config_manager:device_config_manager_workflows",
            args=[self.non_managed_device.pk],
        )
        response = self.client.get(url)
        self.assertHttpStatus(response, 404)


class ManagedDeviceTabViewTestCases:  # pylint: disable=too-few-public-methods
    """Wrapper class for managed-device view tabs."""

    class ManagedDeviceTabViewTestCase(  # pylint: disable=too-many-ancestors
        ConfigManagerViewTestMixin,
        ViewTestCases.ListObjectsViewTestCase,
        ViewTestCases.GetObjectViewTestCase,
    ):
        """Managed-device view tab tests."""

        model: type[Model]
        target_obj_name: str
        empty_target_obj_name: str

        @property
        def target_instance(self):
            """Return target instance."""
            return self.model.objects.get(name=self.target_obj_name)

        @property
        def empty_instance(self):
            """Return target instance."""
            return self.model.objects.get(name=self.empty_target_obj_name)

        @property
        def filter_cond(self):
            """Filter condition to return correct instance."""
            # if self.model == Location:
            #     return Q(device__site=self.target_instance)
            if self.model == Location:
                return Q(device__location=self.target_instance)
            return None

        @classmethod
        def setUpTestData(cls):
            region_type = LocationType.objects.create(name="Region")
            site_type = LocationType.objects.create(name="Site")
            location_status = Status.objects.get_for_model(Location).first()
            device_status = Status.objects.get_for_model(Device).first()
            rack_status = Status.objects.get_for_model(Rack).first()
            cls.region = Location.objects.create(
                name=data.REGION_NAME, location_type=region_type, status=location_status
            )
            cls.site = Location.objects.create(
                name=data.SITE_NAME,
                parent=cls.region,
                location_type=site_type,
                status=location_status,
            )
            cls.site2 = Location.objects.create(
                name=data.SECOND_SITE_NAME,
                parent=cls.region,
                location_type=site_type,
                status=location_status,
            )

            cls.location_type = LocationType.objects.create(name="Building")
            cls.location = Location.objects.create(
                name=data.BUILDING_NAME,
                location_type=cls.location_type,
                status=location_status,
            )
            cls.location2 = Location.objects.create(
                name=data.EMPTY_LOCATION_NAME,
                location_type=cls.location_type,
                status=location_status,
            )

            cls.tenant = Tenant.objects.create(name=data.TENANT_NAME)
            cls.tenant.locations.add(cls.site)

            cls.rack_1 = Rack.objects.create(
                name=data.RACK_1_NAME,
                location=cls.site,
                tenant=cls.tenant,
                status=rack_status,
            )
            cls.rack_2 = Rack.objects.create(
                name=data.RACK_2_NAME,
                location=cls.site,
                tenant=cls.tenant,
                status=rack_status,
            )

            cls.device_role_1 = Role.objects.create(name=data.SPINE_ROLE_NAME)
            cls.device_role_2 = Role.objects.create(name=data.CORE_ROLE_NAME)
            cls.device_role_3 = Role.objects.create(name=data.LEAF_ROLE_NAME)

            cls.manufacturer = Manufacturer.objects.create(name=data.MANUFACTURER_NAME)
            cls.platform = Platform.objects.create(name=data.PLATFORM_NAME, napalm_driver="cumulus")
            cls.device_type = DeviceType.objects.create(
                manufacturer=cls.manufacturer,
                model=data.DEVICE_TYPE_MODEL,
                u_height="2",
            )

            cls.device_1 = Device.objects.create(
                pk="8d475409-6674-4570-afb8-31ce93dc32b3",
                name=data.THIRD_DEVICE_NAME,
                location=cls.site,
                rack=cls.rack_1,
                tenant=cls.tenant,
                device_type=cls.device_type,
                role=cls.device_role_1,
                position="39",
                face="front",
                platform=cls.platform,
                serial="121212",
                status=device_status,
            )

            managed_device_1 = ConfigManagerDeviceStatus.objects.create(device=cls.device_1)
            _ = IntendedConfig.objects.create(
                device_id=managed_device_1,
                config_store_instance=data.CONFIG_STORE_UI_URL,
                path=data.CONFIG_PATH,
                commit_id=data.TEST_INTENDED_COMMIT_ID,
                updated=timezone.now() - timedelta(hours=73),
                updated_by=data.TEST_RENDER_USER,
                commit_message=data.TEST_COMMIT_MESSAGE,
                template_version=data.TEMPLATE_VERSION,
            )
            _ = BackupConfig.objects.create(
                device_id=managed_device_1,
                config_store_instance=data.CONFIG_STORE_UI_URL,
                path=data.BACKUP_CONFIG_PATH,
                commit_id=data.TEST_BACKUP_COMMIT_ID,
                updated=timezone.now() - timedelta(hours=73),
                updated_by=data.TEST_RENDER_USER,
                commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
                workflow_id=data.TEST_WORKFLOW_ID,
                deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
            )

            cls.device_2 = Device.objects.create(
                pk="4ae8ef25-1c9c-4b81-accf-289af4e2aab6",
                name=data.FOURTH_DEVICE_NAME,
                location=cls.site,
                rack=cls.rack_2,
                tenant=cls.tenant,
                device_type=cls.device_type,
                role=cls.device_role_2,
                position="31",
                face="front",
                platform=cls.platform,
                serial="10006",
                status=device_status,
            )

            managed_device2 = ConfigManagerDeviceStatus.objects.create(device=cls.device_2)
            _ = IntendedConfig.objects.create(
                device_id=managed_device2,
                config_store_instance=data.CONFIG_STORE_UI_URL,
                path=data.CONFIG_PATH,
                commit_id=data.TEST_DEPLOYABLE_COMMIT_ID,
                updated=timezone.now(),
                updated_by=data.TEST_RENDER_USER,
                commit_message=data.TEST_COMMIT_MESSAGE,
                template_version=data.TEMPLATE_VERSION,
            )
            _ = BackupConfig.objects.create(
                device_id=managed_device2,
                config_store_instance=data.CONFIG_STORE_UI_URL,
                path=data.BACKUP_CONFIG_PATH,
                commit_id=data.TEST_BACKUP_COMMIT_ID,
                updated=timezone.now() - timedelta(hours=2),
                updated_by=data.TEST_RENDER_USER,
                commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
                workflow_id=data.TEST_WORKFLOW_ID,
                deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
            )

            _ = Device.objects.create(
                pk="93064866-4b25-4372-8ffa-416ac7fc36e5",
                name=data.DEVICE_NAME,
                location=cls.site,
                rack=cls.rack_1,
                tenant=cls.tenant,
                device_type=cls.device_type,
                role=cls.device_role_3,
                position="35",
                face="front",
                platform=cls.platform,
                serial="232323",
                status=device_status,
            )

        def test_list_managed_devices(self):  # pylint: disable=too-many-locals
            """View list of managed devices."""
            obj_perm = ObjectPermission(name="Test permission", actions=["view"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            url = reverse(
                f"plugins:nv_config_manager:{self.model._meta.model_name}_managed_devices",
                args=[self.target_instance.pk],
            )
            response = self.client.get(url)
            self.assertHttpStatus(response, 200)
            response_content = extract_page_body(response.content.decode(response.charset))

            table_data = []
            tbody_content = re.search(r"<tbody>(.*?)</tbody>", response_content, re.DOTALL).group(1)

            row_pattern = r"<tr\s*[^>]*>(.*?)</tr>"
            cell_pattern = r"<td\s*[^>]*>(.*?)</td>"
            rows = re.findall(row_pattern, tbody_content, re.DOTALL)

            for row in rows:
                cells = re.findall(cell_pattern, row, re.DOTALL)
                row_data = [re.sub(r"<[^>]*>", "", cell).strip() for cell in cells]
                table_data.append(row_data)

            managed_device_names = [row[0] for row in table_data]
            managed_devices = ConfigManagerDeviceStatus.objects.filter(self.filter_cond)

            for managed_device in managed_devices:
                self.assertIn(managed_device.device.name, managed_device_names)

        def test_list_managed_devices_empty(self):
            """View list of empty managed devices."""
            obj_perm = ObjectPermission(name="Test permission", actions=["view"])
            obj_perm.save()
            obj_perm.users.add(self.user)
            obj_perm.object_types.add(ContentType.objects.get_for_model(self.model))

            url = reverse(
                f"plugins:nv_config_manager:{self.model._meta.model_name}_managed_devices",
                args=[self.empty_instance.pk],
            )
            response = self.client.get(url)
            self.assertHttpStatus(response, 200)
            response_content = extract_page_body(response.content.decode(response.charset))
            tbody_content = re.search(r"<tbody>(.*?)</tbody>", response_content, re.DOTALL).group(1)

            # using regex grab the empty status messsage.
            pattern = r"&mdash;\s*(.*?)\s*&mdash;"
            message = re.search(pattern, tbody_content).group(1)
            self.assertIn(message, "No Managed Devices found")


class ConfigManagerDeviceStatusBulkAddViewTestCase(ConfigManagerViewTestMixin, TestCase):
    """Tests for the bulk-add managed devices view."""

    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Bulk View Manufacturer")
        site_type = LocationType.objects.create(name="Bulk View Site Type")
        module_type = LocationType.objects.create(name="Bulk View Module Type", parent=site_type)
        location_status = Status.objects.get_for_model(Location).first()
        device_status = Status.objects.get_for_model(Device).first()

        cls.parent_location = Location.objects.create(
            name="Bulk View Parent",
            location_type=site_type,
            status=location_status,
        )
        cls.child_location = Location.objects.create(
            name="Bulk View Child",
            location_type=module_type,
            parent=cls.parent_location,
            status=location_status,
        )
        cls.network_role = Role.objects.create(name="Bulk View Network", color="444444")
        cls.compute_role = Role.objects.create(name="Bulk View Compute", color="555555")
        device_ct = ContentType.objects.get_for_model(Device)
        cls.network_role.content_types.add(device_ct)
        cls.compute_role.content_types.add(device_ct)
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model="Bulk View Model",
        )
        cls.network_device = Device.objects.create(
            name="bulk-view-network",
            location=cls.child_location,
            device_type=cls.device_type,
            role=cls.network_role,
            status=device_status,
        )
        cls.compute_device = Device.objects.create(
            name="bulk-view-compute",
            location=cls.child_location,
            device_type=cls.device_type,
            role=cls.compute_role,
            status=device_status,
        )
        cls.already_managed = Device.objects.create(
            name="bulk-view-managed",
            location=cls.child_location,
            device_type=cls.device_type,
            role=cls.network_role,
            status=device_status,
        )
        ConfigManagerDeviceStatus.objects.create(device=cls.already_managed)

    def setUp(self):
        super().setUp()
        self.url = reverse("plugins:nv_config_manager:configmanagerdevicestatus_add")

    def _grant_add_permission(self):
        obj_perm = ObjectPermission(name="Bulk add permission", actions=["add", "view"])
        obj_perm.save()
        obj_perm.users.add(self.user)
        obj_perm.object_types.add(ContentType.objects.get_for_model(ConfigManagerDeviceStatus))

    def _add_data(self, **overrides):
        data = {
            "location": str(self.parent_location.pk),
            "roles": [str(self.network_role.pk)],
            "devices": [str(self.network_device.pk)],
            "render_enabled": "true",
            "is_aggregate_managed": "true",
        }
        data.update(overrides)
        return data

    def test_get_renders_bulk_add_form(self):
        """GET renders the bulk-add form."""
        self._grant_add_permission()
        response = self.client.get(self.url)
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "Device Filters")
        self.assertContains(response, 'name="devices"')
        self.assertNotContains(response, "Select a Location and one or more Device Roles")

    def test_adds_selected_devices_with_shared_flags(self):
        """Submitting creates managed devices with the selected service flags."""
        self._grant_add_permission()
        response = self.client.post(self.url, self._add_data())
        self.assertHttpStatus(response, 302)
        managed = ConfigManagerDeviceStatus.objects.get(device=self.network_device)
        self.assertTrue(managed.render_enabled)
        self.assertTrue(managed.is_aggregate_managed)
        self.assertFalse(ConfigManagerDeviceStatus.objects.filter(device=self.compute_device).exists())

    def test_records_change_log_for_added_devices(self):
        """Each enrolled device gets a create change-log entry attributed to the user."""
        self._grant_add_permission()
        response = self.client.post(self.url, self._add_data())
        self.assertHttpStatus(response, 302)
        managed = ConfigManagerDeviceStatus.objects.get(device=self.network_device)
        change = ObjectChange.objects.get(
            changed_object_id=managed.pk,
            action=ObjectChangeActionChoices.ACTION_CREATE,
        )
        self.assertEqual(change.user_name, self.user.username)

    def test_adds_only_selected_devices(self):
        """Only the devices chosen in the Devices box are enrolled."""
        self._grant_add_permission()
        second_network = Device.objects.create(
            name="bulk-view-network-2",
            location=self.child_location,
            device_type=self.device_type,
            role=self.network_role,
            status=Status.objects.get_for_model(Device).first(),
        )
        response = self.client.post(self.url, self._add_data(devices=[str(second_network.pk)]))
        self.assertHttpStatus(response, 302)
        self.assertTrue(ConfigManagerDeviceStatus.objects.filter(device=second_network).exists())
        self.assertFalse(ConfigManagerDeviceStatus.objects.filter(device=self.network_device).exists())

    def test_requires_permission(self):
        """Users without add permission cannot access the bulk-add view."""
        response = self.client.get(self.url)
        self.assertHttpStatus(response, 403)

    def test_rejects_ineligible_device(self):
        """A device outside the selected location/role scope is rejected."""
        self._grant_add_permission()
        response = self.client.post(self.url, self._add_data(devices=[str(self.compute_device.pk)]))
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "One or more selected devices are not eligible for enrollment.")
        self.assertFalse(ConfigManagerDeviceStatus.objects.filter(device=self.compute_device).exists())

    def test_rolls_back_on_creation_failure(self):
        """A creation failure rolls back the entire bulk-add transaction."""

        self._grant_add_permission()
        second_network = Device.objects.create(
            name="bulk-view-network-rollback",
            location=self.child_location,
            device_type=self.device_type,
            role=self.network_role,
            status=Status.objects.get_for_model(Device).first(),
        )
        with patch(
            "nv_config_manager.utils.models.ConfigManagerDeviceStatus.objects.bulk_create",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(
                self.url,
                self._add_data(devices=[str(self.network_device.pk), str(second_network.pk)]),
            )
        self.assertHttpStatus(response, 200)
        self.assertContains(response, "Failed to add managed devices")
        self.assertFalse(ConfigManagerDeviceStatus.objects.filter(device=self.network_device).exists())
        self.assertFalse(ConfigManagerDeviceStatus.objects.filter(device=second_network).exists())
