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
"""Tests for nv_config_manager models."""

from datetime import timedelta
from io import StringIO

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.management import call_command
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone
from nautobot.dcim.models import (
    Device,
    DeviceType,
    Location,
    LocationType,
    Manufacturer,
)
from nautobot.extras.models import Role, Status

from nv_config_manager.models import BackupConfig, ConfigManagerDeviceStatus, IntendedConfig
from nv_config_manager.tests.fixtures import mock_data as data


def create_device_environment():
    """Create the shared Nautobot objects used by the model tests."""
    manufacturer, _ = Manufacturer.objects.get_or_create(name=data.MANUFACTURER_NAME)
    site_type, _ = LocationType.objects.get_or_create(name="Site")
    location_status = Status.objects.get_for_model(Location).first()
    device_status = Status.objects.get_for_model(Device).first()
    site, _ = Location.objects.get_or_create(
        name=data.SITE_NAME,
        location_type=site_type,
        status=location_status,
    )
    device_type, _ = DeviceType.objects.get_or_create(
        manufacturer=manufacturer,
        model=data.DEVICE_TYPE_MODEL,
    )
    device_role, _ = Role.objects.get_or_create(
        name=data.LEAF_ROLE_NAME,
        color="ff0000",
    )
    non_managed_device, _ = Device.objects.get_or_create(
        device_type=device_type,
        role=device_role,
        name=data.NON_MANAGED_DEVICE_NAME,
        location=site,
        status=device_status,
    )
    device, _ = Device.objects.get_or_create(
        device_type=device_type,
        role=device_role,
        name=data.DEVICE_NAME,
        location=site,
        status=device_status,
    )
    managed_device, _ = ConfigManagerDeviceStatus.objects.get_or_create(device=device)
    return site, device_type, device_role, non_managed_device, device, managed_device


class ConfigManagerDeviceStatusTestCase(TestCase):
    """Test case for ConfigManagerDeviceStatus model."""

    def setUp(self):
        """Setup objects for the tests."""
        (
            self.site,
            self.device_type,
            self.device_role,
            self.non_managed_device,
            self.device,
            self.managed_device,
        ) = create_device_environment()

    def test_create_managed_device_status_success(self):
        """Succesful creation of managed device."""
        managed_device = ConfigManagerDeviceStatus.objects.create(device=self.non_managed_device)
        self.assertIsNotNone(managed_device)

    def test_create_managed_device_unique_failure(self):
        """Try to instantiate a managed device on an existing device."""
        with self.assertRaises(IntegrityError):
            ConfigManagerDeviceStatus.objects.create(device=self.device)

    def test_delete_managed_device_success(self):
        """Try to delete a managed device."""
        self.assertIsNotNone(self.managed_device)

        self.managed_device.delete()

        with self.assertRaises(ConfigManagerDeviceStatus.DoesNotExist):
            ConfigManagerDeviceStatus.objects.get(pk=self.device.pk)

    def test_is_pending_fail_backup_dne(self):
        """Test is_pending fails due to backup config DNE."""
        self.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        self.assertFalse(self.managed_device.is_pending)

    def test_is_pending_fail_intended_dne(self):
        """Test is_pending fails due to intended config DNE."""
        self.managed_device.backup_config = BackupConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_INTENDED_COMMIT_ID,
        )
        self.assertFalse(self.managed_device.is_pending)

    def test_is_pending_true(self):
        """Test is_pending correctly returns True."""
        self.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        self.managed_device.backup_config = BackupConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now() - timedelta(hours=2),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
        )

        self.assertTrue(self.managed_device.is_pending)

    def test_is_pending_false(self):
        """Test is_pending correctly returns False."""
        self.managed_device.intended_config = IntendedConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_MATCHING_COMMIT_ID,
            updated=timezone.now(),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        self.managed_device.backup_config = BackupConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_BACKUP_COMMIT_ID,
            updated=timezone.now() + timedelta(hours=2),
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_MATCHING_COMMIT_ID,
        )

        self.assertFalse(self.managed_device.is_pending)

    def test_is_aggregate_managed_default_false(self):
        """Test is_aggregate_managed correctly defaults to False."""
        self.assertFalse(self.managed_device.is_aggregate_managed)

    def test_is_aggregate_managed_can_be_set_true(self):
        """Test is_aggregate_managed can be set to True."""
        self.managed_device.is_aggregate_managed = True
        self.managed_device.save()
        self.managed_device.refresh_from_db()
        self.assertTrue(self.managed_device.is_aggregate_managed)

    def test_is_aggregate_managed_can_be_set_false(self):
        """Test is_aggregate_managed can be set to False."""
        self.managed_device.is_aggregate_managed = True
        self.managed_device.save()
        self.managed_device.is_aggregate_managed = False
        self.managed_device.save()
        self.managed_device.refresh_from_db()
        self.assertFalse(self.managed_device.is_aggregate_managed)


class ConfigManagerDeviceStatusOrderingTestCase(TestCase):
    """Guard the total order the DHCP provider's paginated GraphQL reads rely on.

    Nautobot's GraphQL pager is plain limit/offset with no cursor or snapshot, so
    whether consecutive pages tile the table is decided entirely by this model's
    Meta.ordering. Without a unique sort key the database may walk the table
    differently per request and a row can land in two pages or in none.
    """

    def setUp(self):
        """Create enough managed devices to page over."""
        self.site, self.device_type, self.device_role, _, _, _ = create_device_environment()
        for index in range(6):
            device = Device.objects.create(
                device_type=self.device_type,
                role=self.device_role,
                name=f"{data.DEVICE_NAME}-page-{index}",
                location=self.site,
                status=Status.objects.get_for_model(Device).first(),
            )
            ConfigManagerDeviceStatus.objects.create(device=device)

    def test_ordering_breaks_ties_on_a_unique_field(self):
        """Non-unique leading fields are fine; the final tiebreak is what matters.

        Ordering this model by something like device name would sort rows but not
        order them totally, which is the defect tracked upstream in
        nautobot/nautobot#8027.
        """
        ordering = ConfigManagerDeviceStatus._meta.ordering

        self.assertTrue(ordering, "no Meta.ordering: limit/offset pages need not tile")
        self.assertIn(
            ordering[-1].lstrip("-"),
            ("pk", "id"),
            f"Meta.ordering {ordering} has no unique tiebreak; pages may repeat or skip rows",
        )

    def test_paged_reads_return_every_row_exactly_once(self):
        """Walk the model the way the provider does and account for every row.

        Also catches an ordering field that does not resolve, which fails the
        query rather than the ordering assertion above.
        """
        page_size = 4
        collected = []
        offset = 0
        while True:
            page = list(ConfigManagerDeviceStatus.objects.all()[offset : offset + page_size])
            collected.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

        paged_pks = [status.pk for status in collected]
        all_pks = set(ConfigManagerDeviceStatus.objects.values_list("pk", flat=True))

        self.assertEqual(len(paged_pks), len(set(paged_pks)), "a row appeared in two pages")
        self.assertEqual(set(paged_pks), all_pks, "paging skipped rows")


class MigrationStateTestCase(TestCase):
    """Model options must ship with the migration that records them.

    Needs a database despite touching no models: makemigrations checks the
    applied-migration history before it compares model state.
    """

    def test_no_model_changes_are_missing_a_migration(self):
        """The Nautobot pod's init container applies migrations, not model state.

        A Meta change with no migration leaves the deployed database describing
        the old options, so the fix silently does not take effect.
        """
        output = StringIO()

        try:
            call_command(
                "makemigrations",
                "nv_config_manager",
                "--check",
                "--dry-run",
                stdout=output,
                stderr=output,
            )
        except SystemExit:
            self.fail(f"nv_config_manager model changes have no migration:\n{output.getvalue()}")


class IntendedConfigTestCase(TestCase):
    """Test case for IntendedConfig model."""

    def setUp(self):
        """Setup objects for the tests."""
        (
            self.site,
            self.device_type,
            self.device_role,
            self.non_managed_device,
            self.device,
            self.managed_device,
        ) = create_device_environment()

    def test_create_intended_config_success(self):
        """Succesful creation of intended config."""
        try:
            intended_config = self.managed_device.intended_config
        except ObjectDoesNotExist:
            intended_config = None

        self.assertIsNone(intended_config)
        intended_config = IntendedConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated="2024-03-20T03:01:04Z",
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )

        self.assertEqual(self.managed_device.intended_config, intended_config)

    def test_create_intended_config_failure(self):
        """Try to create a new intended config on a managed device with an existing config."""
        try:
            intended_config = self.managed_device.intended_config
        except ObjectDoesNotExist:
            intended_config = None

        self.assertIsNone(intended_config)
        intended_config = IntendedConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated="2024-03-20T03:01:04Z",
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )

        self.assertEqual(self.managed_device.intended_config, intended_config)
        with self.assertRaises(ValidationError):
            intended_config = IntendedConfig.objects.create(
                device_id=self.managed_device,
                config_store_instance=data.CONFIG_STORE_UI_URL,
                path=data.CONFIG_PATH,
                commit_id=data.TEST_PREVIOUS_COMMIT_ID,
                updated="2024-04-20T03:01:04Z",
                updated_by=data.TEST_EVENT_USER,
                commit_message="new commit 2",
                template_version=data.TEMPLATE_VERSION,
            )

    def test_edit_intended_config_success(self):
        """Try to edit an existing intended config."""
        intended_config = IntendedConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.CONFIG_PATH,
            commit_id=data.TEST_INTENDED_COMMIT_ID,
            updated="2024-03-20T03:01:04Z",
            updated_by=data.TEST_RENDER_USER,
            commit_message=data.TEST_COMMIT_MESSAGE,
            template_version=data.TEMPLATE_VERSION,
        )
        self.assertEqual(self.managed_device.intended_config, intended_config)
        intended_config.commit_id = data.TEST_UPDATED_COMMIT_ID
        intended_config.updated = "2024-03-21T03:01:04Z"
        intended_config.commit_message = "edited commit"
        intended_config.save()

        updated_intended_config = IntendedConfig.objects.get(pk=intended_config.pk)

        self.assertEqual(int(updated_intended_config.commit_id), data.TEST_UPDATED_COMMIT_ID)
        self.assertEqual(updated_intended_config.commit_message, "edited commit")
        self.assertEqual(self.managed_device.intended_config, updated_intended_config)


class BackupConfigTestCase(TestCase):
    """Test case for BackupConfig model."""

    def setUp(self):
        """Setup objects for the tests."""
        (
            self.site,
            self.device_type,
            self.device_role,
            self.non_managed_device,
            self.device,
            self.managed_device,
        ) = create_device_environment()


def test_create_backup_config_success(self):
    """Successful creation of backup config."""
    try:
        backup_config = self.managed_device.backup_config
    except ObjectDoesNotExist:
        backup_config = None

    self.assertIsNone(backup_config)
    backup_config = BackupConfig.objects.create(
        device_id=self.managed_device,
        path=data.BACKUP_CONFIG_PATH,
        commit_id=data.TEST_BACKUP_COMMIT_ID,
        updated="2024-03-20T03:01:04Z",
        updated_by=data.TEST_RENDER_USER,
        commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
        workflow_id=data.TEST_WORKFLOW_ID,
        deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
    )

    self.assertEqual(self.managed_device.backup_config, backup_config)


def test_create_backup_config_failure(self):
    """Try to create a new backup config on a managed device with an existing config."""
    try:
        backup_config = self.managed_device.backup_config
    except ObjectDoesNotExist:
        backup_config = None

    self.assertIsNone(backup_config)
    backup_config = BackupConfig.objects.create(
        device_id=self.managed_device,
        config_store_instance=data.CONFIG_STORE_UI_URL,
        path=data.BACKUP_CONFIG_PATH,
        commit_id=data.TEST_BACKUP_COMMIT_ID,
        updated="2024-03-20T03:01:04Z",
        updated_by=data.TEST_RENDER_USER,
        commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
        workflow_id=data.TEST_WORKFLOW_ID,
        deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
    )

    self.assertEqual(self.managed_device.backup_config, backup_config)

    with self.assertRaises(ValidationError):
        backup_config = BackupConfig.objects.create(
            device_id=self.managed_device,
            config_store_instance=data.CONFIG_STORE_UI_URL,
            path=data.BACKUP_CONFIG_PATH,
            commit_id=data.TEST_PREVIOUS_COMMIT_ID,
            updated="2024-04-20T03:01:04Z",
            updated_by=data.TEST_EVENT_USER,
            commit_message="new backup commit 2",
            workflow_id=data.TEST_WORKFLOW_ID,
            deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
        )


def test_edit_backup_config_success(self):
    """Try to edit an existing backup config."""
    backup_config = BackupConfig.objects.create(
        device_id=self.managed_device,
        path=data.BACKUP_CONFIG_PATH,
        commit_id=data.TEST_BACKUP_COMMIT_ID,
        updated="2024-03-20T03:01:04Z",
        updated_by=data.TEST_RENDER_USER,
        commit_message=data.TEST_BACKUP_COMMIT_MESSAGE,
        workflow_id=data.TEST_WORKFLOW_ID,
        deployed_commit_id=data.TEST_PENDING_DEPLOYED_COMMIT_ID,
    )
    self.assertEqual(self.managed_device.backup_config, backup_config)
    backup_config.commit_id = data.TEST_UPDATED_COMMIT_ID
    backup_config.updated = "2024-03-21T03:01:04Z"
    backup_config.commit_message = "edited backup commit"
    backup_config.deployed_commit_id = data.TEST_UPDATED_DEPLOYED_COMMIT_ID
    backup_config.save()

    updated_backup_config = BackupConfig.objects.get(pk=backup_config.pk)

    self.assertEqual(int(updated_backup_config.commit_id), data.TEST_UPDATED_COMMIT_ID)
    self.assertEqual(updated_backup_config.commit_message, "edited backup commit")
    self.assertEqual(int(updated_backup_config.deployed_commit_id), data.TEST_UPDATED_DEPLOYED_COMMIT_ID)
    self.assertEqual(self.managed_device.backup_config, updated_backup_config)
