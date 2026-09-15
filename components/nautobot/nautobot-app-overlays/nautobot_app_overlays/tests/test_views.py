#  SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for Overlays views."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from nautobot.dcim.models import Interface
from nautobot.extras.models import CustomField, Status

from nautobot_app_overlays import forms, models
from nautobot_app_overlays.tests.fixtures import (
    create_assignment_test_data,
    create_device_test_data,
    create_overlay_test_data,
    create_pkey_test_data,
    create_vxlan_test_data,
)

User = get_user_model()


class OverlayViewTestCase(TestCase):
    """Test cases for Overlay views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        create_overlay_test_data(cls)
        create_device_test_data(cls)
        cls.user = User.objects.create_superuser(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

    def setUp(self):
        """Set up test client."""
        self.client.force_login(self.user)

    def test_overlay_list_view(self):
        """Test the Overlay list view renders correctly."""
        url = reverse("plugins:nautobot_app_overlays:overlay_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Overlay 1")
        self.assertContains(response, "Test Overlay 2")

    def test_overlay_detail_view(self):
        """Test the Overlay detail view renders correctly."""
        overlay = self.overlays[0]
        url = reverse(
            "plugins:nautobot_app_overlays:overlay",
            kwargs={"pk": overlay.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, overlay.name)
        self.assertContains(response, "Overlay Assignments")

    def test_overlay_detail_view_with_assignments(self):
        """Test the detail view shows overlay assignments."""
        overlay = self.overlays[0]
        device_ct = ContentType.objects.get(app_label="dcim", model="device")

        models.OverlayAssignment.objects.create(
            overlay=overlay,
            assigned_object_type=device_ct,
            assigned_object_id=self.devices[0].pk,
            status=self.assignment_status,
        )

        url = reverse(
            "plugins:nautobot_app_overlays:overlay",
            kwargs={"pk": overlay.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Overlay Assignments")
        self.assertContains(response, 'class="panel-body table-responsive"')

    def test_overlay_add_view(self):
        """Test the Overlay add view."""
        url = reverse("plugins:nautobot_app_overlays:overlay_add")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_overlay_edit_view(self):
        """Test the Overlay edit view."""
        overlay = self.overlays[0]
        url = reverse(
            "plugins:nautobot_app_overlays:overlay_edit",
            kwargs={"pk": overlay.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class BulkAllocationViewTestCase(TestCase):
    """Test cases for bulk allocation views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        create_overlay_test_data(cls)
        create_device_test_data(cls)
        cls.user = User.objects.create_superuser(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

    def setUp(self):
        """Set up test client."""
        self.client.force_login(self.user)

    def test_bulk_allocate_get(self):
        """Test the bulk allocation form renders correctly."""
        overlay = self.overlays[0]
        url = reverse(
            "plugins:nautobot_app_overlays:overlay_bulk_allocate",
            kwargs={"pk": overlay.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bulk Allocate")
        self.assertContains(response, overlay.name)
        self.assertContains(response, "Devices")
        self.assertContains(response, "Interfaces")
        self.assertContains(response, "Racks")

    def test_bulk_allocate_post_success(self):
        """Test successful bulk allocation."""
        overlay = self.overlays[0]

        url = reverse(
            "plugins:nautobot_app_overlays:overlay_bulk_allocate",
            kwargs={"pk": overlay.pk},
        )

        data = {
            "devices": [self.devices[0].pk, self.devices[1].pk],
            "interfaces": [],
            "racks": [],
            "role": "compute",
        }

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        assignments = models.OverlayAssignment.objects.filter(overlay=overlay)
        self.assertEqual(assignments.count(), 2)

    def test_bulk_allocate_post_no_selection(self):
        """Test bulk allocation with no objects selected."""
        overlay = self.overlays[0]

        url = reverse(
            "plugins:nautobot_app_overlays:overlay_bulk_allocate",
            kwargs={"pk": overlay.pk},
        )

        data = {
            "devices": [],
            "interfaces": [],
            "racks": [],
            "role": "",
        }

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please select at least one")

    def test_bulk_allocate_skips_existing_assignments(self):
        """Test that bulk allocation skips already existing assignments."""
        overlay = self.overlays[0]
        device_ct = ContentType.objects.get(app_label="dcim", model="device")

        models.OverlayAssignment.objects.create(
            overlay=overlay,
            assigned_object_type=device_ct,
            assigned_object_id=self.devices[0].pk,
            status=self.assignment_status,
        )

        url = reverse(
            "plugins:nautobot_app_overlays:overlay_bulk_allocate",
            kwargs={"pk": overlay.pk},
        )

        data = {
            "devices": [self.devices[0].pk, self.devices[1].pk],
            "interfaces": [],
            "racks": [],
            "role": "",
        }

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        assignments = models.OverlayAssignment.objects.filter(overlay=overlay)
        self.assertEqual(assignments.count(), 2)


class BulkDeallocateViewTestCase(TestCase):
    """Test cases for bulk deallocation views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        create_assignment_test_data(cls)
        cls.user = User.objects.create_superuser(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

    def setUp(self):
        """Set up test client."""
        self.client.force_login(self.user)

    def test_bulk_deallocate_success(self):
        """Test successful bulk deallocation."""
        overlay = self.overlays[0]
        assignment = self.assignments[0]

        url = reverse(
            "plugins:nautobot_app_overlays:overlay_bulk_deallocate",
            kwargs={"pk": overlay.pk},
        )

        data = {"pk": [str(assignment.pk)]}

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        self.assertFalse(models.OverlayAssignment.objects.filter(pk=assignment.pk).exists())

    def test_bulk_deallocate_no_assignments_selected(self):
        """Test bulk deallocation with no assignments selected."""
        overlay = self.overlays[0]

        url = reverse(
            "plugins:nautobot_app_overlays:overlay_bulk_deallocate",
            kwargs={"pk": overlay.pk},
        )

        data = {"pk": []}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)


class VXLANViewTestCase(TestCase):
    """Test cases for VXLAN views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        create_vxlan_test_data(cls)
        cls.user = User.objects.create_superuser(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

    def setUp(self):
        """Set up test client."""
        self.client.force_login(self.user)

    def test_vxlan_list_view(self):
        """Test the VXLAN list view renders correctly."""
        url = reverse("plugins:nautobot_app_overlays:vxlan_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test VXLAN 1")

    def test_vxlan_detail_view(self):
        """Test the VXLAN detail view renders correctly."""
        vxlan = self.vxlans[0]
        url = reverse(
            "plugins:nautobot_app_overlays:vxlan",
            kwargs={"pk": vxlan.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, vxlan.name)
        self.assertContains(response, str(vxlan.vnid))

    def test_vxlan_list_view_with_filter(self):
        """Test the VXLAN list view with filters."""
        url = reverse("plugins:nautobot_app_overlays:vxlan_list")
        response = self.client.get(url, {"vnid__gte": 10000, "vnid__lte": 11000})
        self.assertEqual(response.status_code, 200)


class InfiniBandPKeyViewTestCase(TestCase):
    """Test cases for InfiniBandPKey views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        create_pkey_test_data(cls)
        cls.user = User.objects.create_superuser(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

    def setUp(self):
        """Set up test client."""
        self.client.force_login(self.user)

    def test_pkey_list_view(self):
        """Test the InfiniBandPKey list view renders correctly."""
        url = reverse("plugins:nautobot_app_overlays:infinibandpkey_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test PKey 1")

    def test_pkey_detail_view(self):
        """Test the InfiniBandPKey detail view renders correctly."""
        pkey = self.pkeys[0]
        url = reverse(
            "plugins:nautobot_app_overlays:infinibandpkey",
            kwargs={"pk": pkey.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pkey.name)
        self.assertContains(response, pkey.pkey)


class OverlayAssignmentViewTestCase(TestCase):
    """Test cases for OverlayAssignment views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data."""
        create_assignment_test_data(cls)
        cls.user = User.objects.create_superuser(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

    def setUp(self):
        """Set up test client."""
        self.client.force_login(self.user)

    def test_assignment_list_view(self):
        """Test the OverlayAssignment list view renders correctly."""
        url = reverse("plugins:nautobot_app_overlays:overlayassignment_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_assignment_list_view_filtered_by_overlay(self):
        """Test the OverlayAssignment list view filtered by overlay."""
        url = reverse("plugins:nautobot_app_overlays:overlayassignment_list")
        overlay = self.overlays[0]
        response = self.client.get(url, {"overlay": overlay.pk})
        self.assertEqual(response.status_code, 200)


class OverlayAssignmentCreateViewTestCase(TestCase):
    """Tests for the type-aware OverlayAssignmentCreateView."""

    @classmethod
    def setUpTestData(cls):
        """Set up overlays, a device, and an interface for form submission tests."""
        create_overlay_test_data(cls)
        create_device_test_data(cls)

        cls.user = User.objects.create_superuser(
            username="assign_testuser",
            email="assign@example.com",
            password="testpassword",
        )

        iface_status = Status.objects.get_for_model(Interface).first()
        cls.interface, _ = Interface.objects.get_or_create(
            device=cls.devices[0],
            name="eth-test",
            defaults={"type": "1000base-t", "status": iface_status},
        )

        interface_ct = ContentType.objects.get(app_label="dcim", model="interface")
        cf, _ = CustomField.objects.get_or_create(
            key="ib_guid",
            defaults={"type": "text", "label": "InfiniBand GUID"},
        )
        if interface_ct not in cf.content_types.all():
            cf.content_types.add(interface_ct)
        cls.interface.cf["ib_guid"] = "0002c903000e0b72"
        cls.interface.save()

        cls.interface_no_guid, _ = Interface.objects.get_or_create(
            device=cls.devices[0],
            name="eth-no-guid",
            defaults={"type": "1000base-t", "status": iface_status},
        )

        cls.vxlan_overlay = cls.overlays[0]  # isolation_type=VXLAN_EVPN
        cls.ib_pkey_overlay = cls.overlays[2]  # isolation_type=IB_PKEY

    def setUp(self):
        self.client.force_login(self.user)

    def _url(self, overlay):
        return reverse(
            "plugins:nautobot_app_overlays:overlay_assignment_create",
            kwargs={"pk": overlay.pk},
        )

    # --- GET: form dispatch ---

    def test_get_renders_ib_pkey_form_for_ib_pkey_overlay(self):
        """GET for an IB PKey overlay renders IBPKeyOverlayAssignmentForm."""
        response = self.client.get(self._url(self.ib_pkey_overlay))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["form"], forms.IBPKeyOverlayAssignmentForm)

    def test_get_renders_general_form_for_vxlan_overlay(self):
        """GET for a VXLAN overlay renders GeneralOverlayAssignmentForm."""
        response = self.client.get(self._url(self.vxlan_overlay))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["form"], forms.GeneralOverlayAssignmentForm)

    def test_get_shows_overlay_name_in_response(self):
        """GET response contains the overlay name in the heading."""
        response = self.client.get(self._url(self.ib_pkey_overlay))
        self.assertContains(response, self.ib_pkey_overlay.name)

    def test_get_nonexistent_overlay_returns_404(self):
        """GET with an unknown overlay UUID returns 404."""
        url = reverse(
            "plugins:nautobot_app_overlays:overlay_assignment_create",
            kwargs={"pk": uuid.uuid4()},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # --- POST: IB PKey assignment ---

    def test_post_ib_pkey_creates_interface_assignment(self):
        """Valid POST for IB PKey overlay creates an assignment with auto-populated GUID."""
        data = {
            "overlay": self.ib_pkey_overlay.pk,
            "interface": self.interface.pk,
            "status": self.assignment_status.pk,
        }
        response = self.client.post(self._url(self.ib_pkey_overlay), data)
        self.assertRedirects(response, self.ib_pkey_overlay.get_absolute_url(), fetch_redirect_response=False)

        assignment = models.OverlayAssignment.objects.filter(
            overlay=self.ib_pkey_overlay,
            assigned_object_id=self.interface.pk,
        ).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.guid, "0002c903000e0b72")
        assignment.delete()

    def test_post_ib_pkey_interface_without_guid_rerenders_form(self):
        """POST for IB PKey overlay with an interface missing cf_ib_guid re-renders with errors."""
        data = {
            "overlay": self.ib_pkey_overlay.pk,
            "interface": self.interface_no_guid.pk,
            "status": self.assignment_status.pk,
        }
        response = self.client.post(self._url(self.ib_pkey_overlay), data)
        self.assertEqual(response.status_code, 200)

    # --- POST: General (VXLAN) assignment ---

    def test_post_general_creates_device_assignment(self):
        """Valid POST for a VXLAN overlay creates a device assignment."""
        data = {
            "overlay": self.vxlan_overlay.pk,
            "object_type": "device",
            "device": self.devices[1].pk,
            "status": self.assignment_status.pk,
        }
        response = self.client.post(self._url(self.vxlan_overlay), data)
        self.assertRedirects(response, self.vxlan_overlay.get_absolute_url(), fetch_redirect_response=False)

        assignment = models.OverlayAssignment.objects.filter(
            overlay=self.vxlan_overlay,
            assigned_object_id=self.devices[1].pk,
        ).first()
        self.assertIsNotNone(assignment)
        assignment.delete()

    def test_post_general_missing_device_rerenders_form(self):
        """POST for VXLAN overlay with object_type=device but no device re-renders form."""
        data = {
            "overlay": self.vxlan_overlay.pk,
            "object_type": "device",
            "status": self.assignment_status.pk,
        }
        response = self.client.post(self._url(self.vxlan_overlay), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("device", response.context["form"].errors)
