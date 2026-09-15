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

"""Tests for Overlays tables."""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django_tables2 import RequestConfig
from nautobot.ipam.models import VRF

from nautobot_app_overlays import models, tables
from nautobot_app_overlays.tests.fixtures import (
    create_assignment_test_data,
    create_overlay_test_data,
    create_pkey_test_data,
    create_vxlan_test_data,
    get_or_create_status_for_model,
)


class OverlayTableTestCase(TestCase):
    """Tests for OverlayTable."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)
        cls.factory = RequestFactory()

    def test_table_renders_all_rows(self):
        """Table renders one row per overlay."""
        table = tables.OverlayTable(models.Overlay.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)
        self.assertEqual(len(table.rows), models.Overlay.objects.count())


class AssignmentCountColumnTestCase(TestCase):
    """The Assignments column always links to the filtered list, regardless of count.

    Nautobot core's LinkedCountColumn links a count of 1 to the (un-templated,
    dead-end) assignment detail page while a count > 1 links to the filtered
    list. AssignmentCountColumn must link to the filtered list in both cases.
    """

    @classmethod
    def setUpTestData(cls):
        create_assignment_test_data(cls)
        cls.factory = RequestFactory()
        # overlays[0] has 2 assignments, overlays[1] has 1, overlays[2] has 0.
        cls.list_url = reverse("plugins:nautobot_app_overlays:overlayassignment_list")

    def _rendered_cells(self):
        """Render OverlayTable the way the list view does (annotated count).

        BaseTable injects the ``assignments_list`` prefetch for the linked-count
        column, which is what makes core's column reach for the single-record
        detail link -- so this faithfully reproduces the buggy condition.
        """
        queryset = models.Overlay.objects.annotate(assignment_count=Count("assignments")).order_by("name")
        table = tables.OverlayTable(queryset)
        RequestConfig(self.factory.get("/")).configure(table)
        return {row.record.pk: row.get_cell("assignment_count") for row in table.rows}

    def test_single_assignment_links_to_filtered_list_not_detail(self):
        """A count of 1 links to the filtered list, not the assignment detail page."""
        overlay = self.overlays[1]
        cell = self._rendered_cells()[overlay.pk]
        self.assertIn(f"{self.list_url}?overlay={overlay.pk}", cell)
        self.assertNotIn(self.assignments[2].get_absolute_url(), cell)

    def test_multiple_assignments_link_to_filtered_list(self):
        """A count > 1 links to the filtered list (unchanged from core)."""
        overlay = self.overlays[0]
        cell = self._rendered_cells()[overlay.pk]
        self.assertIn(f"{self.list_url}?overlay={overlay.pk}", cell)
        self.assertIn(">2<", cell)

    def test_zero_assignments_render_placeholder(self):
        """A count of 0 renders a placeholder, not a link."""
        overlay = self.overlays[2]
        cell = self._rendered_cells()[overlay.pk]
        self.assertNotIn("<a ", cell)


class OverlayAssignmentTableTestCase(TestCase):
    """Tests for OverlayAssignmentTable."""

    @classmethod
    def setUpTestData(cls):
        create_assignment_test_data(cls)
        cls.factory = RequestFactory()
        cls.vrf = VRF.objects.create(
            name="SpXTenant60000",
            namespace=cls.namespace,
            status=get_or_create_status_for_model(VRF),
        )
        cls.vrf_assignment = models.OverlayAssignment.objects.create(
            overlay=cls.overlays[0],
            assigned_object_type=ContentType.objects.get_for_model(VRF),
            assigned_object_id=cls.vrf.pk,
            status=cls.assignment_status,
        )

    def test_table_renders_all_rows(self):
        """Table renders one row per assignment."""
        table = tables.OverlayAssignmentTable(models.OverlayAssignment.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)
        self.assertEqual(len(table.rows), models.OverlayAssignment.objects.count())

    def test_vrf_assignment_links_to_vrf(self):
        """The default Member column links a VRF assignment to its VRF detail page."""
        table = tables.OverlayAssignmentTable(models.OverlayAssignment.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)

        self.assertTrue(table.columns["assigned_object"].visible)
        row = next(row for row in table.rows if row.record == self.vrf_assignment)
        self.assertIn(self.vrf.get_absolute_url(), row.get_cell("assigned_object"))


class OverlayAssignmentInlineTableTestCase(TestCase):
    """Tests for OverlayAssignmentInlineTable — used inside overlay detail views."""

    @classmethod
    def setUpTestData(cls):
        create_assignment_test_data(cls)
        cls.factory = RequestFactory()
        cls.vrf = VRF.objects.create(
            name="SpXTenant60000",
            namespace=cls.namespace,
            status=get_or_create_status_for_model(VRF),
        )
        cls.vrf_assignment = models.OverlayAssignment.objects.create(
            overlay=cls.overlays[0],
            assigned_object_type=ContentType.objects.get_for_model(VRF),
            assigned_object_id=cls.vrf.pk,
            status=cls.assignment_status,
        )

    def test_inline_table_excludes_overlay_column(self):
        """Inline table omits the overlay column (redundant inside the overlay detail view)."""
        table = tables.OverlayAssignmentInlineTable(models.OverlayAssignment.objects.all())
        self.assertNotIn("overlay", table.columns.names())

    def test_vrf_assignment_links_to_vrf(self):
        """The overlay detail table links a VRF assignment to its VRF detail page."""
        table = tables.OverlayAssignmentInlineTable(models.OverlayAssignment.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)

        self.assertTrue(table.columns["assigned_object"].visible)
        row = next(row for row in table.rows if row.record == self.vrf_assignment)
        self.assertIn(self.vrf.get_absolute_url(), row.get_cell("assigned_object"))


class VXLANTableTestCase(TestCase):
    """Tests for VXLANTable."""

    @classmethod
    def setUpTestData(cls):
        create_vxlan_test_data(cls)
        cls.factory = RequestFactory()

    def test_table_renders_all_rows(self):
        """Table renders one row per VXLAN."""
        table = tables.VXLANTable(models.VXLAN.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)
        self.assertEqual(len(table.rows), models.VXLAN.objects.count())


class InfiniBandPKeyTableTestCase(TestCase):
    """Tests for InfiniBandPKeyTable."""

    @classmethod
    def setUpTestData(cls):
        create_pkey_test_data(cls)
        cls.factory = RequestFactory()

    def test_table_renders_all_rows(self):
        """Table renders one row per PKey."""
        table = tables.InfiniBandPKeyTable(models.InfiniBandPKey.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)
        self.assertEqual(len(table.rows), models.InfiniBandPKey.objects.count())


class OverlayMembershipInlineTableTestCase(TestCase):
    """Tests for OverlayMembershipInlineTable — shown on Device/Interface/VRF/VLAN detail views."""

    @classmethod
    def setUpTestData(cls):
        create_assignment_test_data(cls)
        cls.factory = RequestFactory()

    def test_inline_table_renders_all_rows(self):
        """Table renders one row per assignment."""
        table = tables.OverlayMembershipInlineTable(models.OverlayAssignment.objects.all())
        RequestConfig(self.factory.get("/")).configure(table)
        self.assertEqual(len(table.rows), models.OverlayAssignment.objects.count())

    def test_inline_table_excludes_action_and_object_columns(self):
        """Inline table omits pk, actions, and assigned_object columns — irrelevant on a host detail view."""
        table = tables.OverlayMembershipInlineTable(models.OverlayAssignment.objects.all())
        cols = list(table.columns.names())
        for excluded in ("pk", "actions", "assigned_object", "assigned_object_type"):
            self.assertNotIn(excluded, cols)
