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

"""Tests for Overlays forms."""

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from nautobot.dcim.models import Device, Interface
from nautobot.extras.models import CustomField, Status

from nautobot_app_overlays import forms
from nautobot_app_overlays.choices import (
    IsolationTypeChoices,
    OverlayAssignmentRoleChoices,
    PKeyMembershipTypeChoices,
)
from nautobot_app_overlays.models import OverlayAssignment
from nautobot_app_overlays.tests.fixtures import (
    create_device_test_data,
    create_overlay_test_data,
)


def _create_interface(device, name="eth0", ib_guid=""):
    """Create a test Interface on the given device, optionally setting cf_ib_guid."""
    iface_status = Status.objects.get_for_model(Interface).first()
    iface_type = "1000base-t"
    iface, _ = Interface.objects.get_or_create(
        device=device,
        name=name,
        defaults={"type": iface_type, "status": iface_status},
    )
    if ib_guid:
        _ensure_ib_guid_custom_field()
        iface.cf["ib_guid"] = ib_guid
        iface.save()
    return iface


def _ensure_ib_guid_custom_field():
    """Ensure the ib_guid CustomField exists and is attached to dcim.Interface."""
    interface_ct = ContentType.objects.get(app_label="dcim", model="interface")
    cf, _ = CustomField.objects.get_or_create(
        key="ib_guid",
        defaults={"type": "text", "label": "InfiniBand GUID"},
    )
    if interface_ct not in cf.content_types.all():
        cf.content_types.add(interface_ct)


class OverlayFormTestCase(TestCase):
    """Tests for OverlayForm validation."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)

    def test_form_valid_data(self):
        """Valid overlay form submits successfully."""
        form = forms.OverlayForm(
            data={
                "name": "New Test Overlay",
                "tenant": self.tenant.pk,
                "location": self.location.pk,
                "isolation_type": IsolationTypeChoices.VXLAN_EVPN,
                "status": self.overlay_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_missing_required_fields(self):
        """Form rejects submission with no data and reports all required fields."""
        form = forms.OverlayForm(data={})
        self.assertFalse(form.is_valid())
        for field in ("name", "tenant", "location", "isolation_type"):
            self.assertIn(field, form.errors)

    def test_filter_form_isolation_type_choices(self):
        """Isolation type filter contains all defined choices."""
        field = forms.OverlayFilterForm().fields["isolation_type"]
        for value, label in IsolationTypeChoices.CHOICES:
            self.assertIn((value, label), field.choices)


class TypedOverlayFormIsolationTypeTestCase(TestCase):
    """Tests that each typed overlay form sets isolation_type correctly.

    Regression test for the ModelForm initial-override bug: NautobotModelForm
    populates self.initial from the model instance on __init__, which overwrites
    field-level ``initial`` values.  Each typed form now forces the correct
    value in its own __init__.
    """

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)

    def _assert_hidden_value(self, form_class, expected_value):
        """Assert an unbound form renders isolation_type with the expected value."""
        form = form_class()
        self.assertEqual(
            form["isolation_type"].value(),
            expected_value,
            f"{form_class.__name__} isolation_type initial value is wrong",
        )
        rendered = form["isolation_type"].as_widget()
        self.assertIn(f'value="{expected_value}"', rendered, f"{form_class.__name__} rendered value missing")

    def test_ib_pkey_overlay_form_initial_value(self):
        """IBPKeyOverlayForm sets isolation_type to ib_pkey."""
        self._assert_hidden_value(forms.IBPKeyOverlayForm, IsolationTypeChoices.IB_PKEY)

    def test_vxlan_overlay_form_initial_value(self):
        """VXLANOverlayForm sets isolation_type to vxlan_evpn."""
        self._assert_hidden_value(forms.VXLANOverlayForm, IsolationTypeChoices.VXLAN_EVPN)

    def test_ib_mkey_overlay_form_initial_value(self):
        """IBMKeyOverlayForm sets isolation_type to ib_mkey."""
        self._assert_hidden_value(forms.IBMKeyOverlayForm, IsolationTypeChoices.IB_MKEY)

    def test_nvlink_partition_overlay_form_initial_value(self):
        """NVLinkPartitionOverlayForm sets isolation_type to nvlink_partition."""
        self._assert_hidden_value(forms.NVLinkPartitionOverlayForm, IsolationTypeChoices.NVLINK_PARTITION)

    def test_spectrum_x_overlay_form_initial_value(self):
        """SpectrumXOverlayForm sets isolation_type to spectrum_x_vrf."""
        self._assert_hidden_value(forms.SpectrumXOverlayForm, IsolationTypeChoices.SPECTRUM_X_VRF)

    def test_ib_pkey_overlay_form_submits_successfully(self):
        """IBPKeyOverlayForm is valid and saves when all required fields are provided."""
        form = forms.IBPKeyOverlayForm(
            data={
                "name": "test-ibpkey-submit",
                "isolation_type": IsolationTypeChoices.IB_PKEY,
                "tenant": self.tenant.pk,
                "location": self.location.pk,
                "status": self.overlay_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.isolation_type, IsolationTypeChoices.IB_PKEY)
        obj.delete()

    def test_ib_pkey_overlay_form_fails_without_isolation_type(self):
        """IBPKeyOverlayForm rejects submission when isolation_type is absent."""
        form = forms.IBPKeyOverlayForm(
            data={
                "name": "test-no-isolation-type",
                "tenant": self.tenant.pk,
                "location": self.location.pk,
                "status": self.overlay_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("isolation_type", form.errors)


class VXLANFormTestCase(TestCase):
    """Tests for VXLANForm validation."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)

    def test_form_valid_data(self):
        """Valid VXLAN form submits successfully."""
        form = forms.VXLANForm(
            data={
                "vnid": 10001,
                "name": "Test VXLAN",
                "vni_type": "l2",
                "namespace": self.namespace.pk,
                "status": self.vxlan_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_vnid_max_boundary(self):
        """VNID at maximum value (16777215) is accepted."""
        form = forms.VXLANForm(
            data={
                "vnid": 16777215,
                "name": "Max VNI",
                "vni_type": "l2",
                "namespace": self.namespace.pk,
                "status": self.vxlan_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_vnid_range_fields(self):
        """Filter form VNI range fields accept valid range."""
        form = forms.VXLANFilterForm(data={"vnid__gte": 10000, "vnid__lte": 20000})
        self.assertTrue(form.is_valid())


class InfiniBandPKeyFormTestCase(TestCase):
    """Tests for InfiniBandPKeyForm validation."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)

    def test_form_valid_data(self):
        """Valid PKey form submits successfully."""
        form = forms.InfiniBandPKeyForm(
            data={
                "pkey": "0x8001",
                "name": "Test PKey",
                "membership_type": PKeyMembershipTypeChoices.FULL,
                "status": self.pkey_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class OverlayAssignmentFormTestCase(TestCase):
    """Tests for OverlayAssignmentForm validation."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)
        create_device_test_data(cls)
        cls.interface = _create_interface(cls.devices[0])

    def test_form_valid_data_with_device(self):
        """Valid assignment form with a device submits successfully."""
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[0].pk,
                "object_type": "device",
                "device": self.devices[0].pk,
                "role": OverlayAssignmentRoleChoices.COMPUTE,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_missing_object_type(self):
        """Assignment form rejects submission without object_type."""
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[0].pk,
                "device": self.devices[0].pk,
                "role": OverlayAssignmentRoleChoices.COMPUTE,
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("object_type", form.errors)

    def test_object_type_field_includes_assignable_types(self):
        """object_type field includes device, interface, and rack choices."""
        choices = [c[0] for c in forms.OverlayAssignmentForm().fields["object_type"].choices]
        for expected in ("device", "interface", "rack"):
            self.assertIn(expected, choices)

    def test_ib_pkey_overlay_rejects_non_interface_object_type(self):
        """IB PKey overlays only accept interface assignments."""
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[2].pk,  # isolation_type=IB_PKEY
                "object_type": "device",
                "device": self.devices[0].pk,
                "guid": "0002c903000e0b72",
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "IB PKey overlays can only have Interface assignments. Please select an interface.",
            form.non_field_errors(),
        )

    def test_ib_pkey_overlay_requires_guid(self):
        """IB PKey overlay assignments require a GUID."""
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[2].pk,  # isolation_type=IB_PKEY
                "object_type": "interface",
                "interface": self.interface.pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("GUID is required for IB PKey overlay assignments.", form.errors["guid"])

    def test_vxlan_overlay_rejects_guid_and_membership_type(self):
        """Non-IB-PKey overlays flag both GUID and membership type when set."""
        isolation_type = self.overlays[0].isolation_type  # VXLAN_EVPN
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[0].pk,
                "object_type": "device",
                "device": self.devices[0].pk,
                "guid": "0002c903000e0b72",
                "membership_type": PKeyMembershipTypeChoices.FULL,
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn(f"GUID should not be set for {isolation_type} overlay assignments.", form.errors["guid"])
        self.assertIn(
            f"Membership type should not be set for {isolation_type} overlay assignments.",
            form.errors["membership_type"],
        )

    def test_missing_object_for_selected_type_is_invalid(self):
        """Selecting an object type without picking the object raises a type-specific error."""
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[0].pk,
                "object_type": "interface",
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Please select an interface.", form.non_field_errors())

    def test_edit_form_prepopulates_interface_and_parent_device(self):
        """Editing an interface assignment pre-selects the object type, interface, and its device."""
        assignment = OverlayAssignment.objects.create(
            overlay=self.overlays[0],
            assigned_object_type=ContentType.objects.get_for_model(Interface),
            assigned_object_id=self.interface.pk,
            status=self.assignment_status,
        )
        form = forms.OverlayAssignmentForm(instance=assignment)
        self.assertEqual(form.initial["object_type"], "interface")
        self.assertEqual(form.initial["interface"], self.interface)
        self.assertEqual(form.initial["device"], self.devices[0])

    def test_edit_form_prepopulates_device(self):
        """Editing a device assignment pre-selects the object type and device."""
        assignment = OverlayAssignment.objects.create(
            overlay=self.overlays[0],
            assigned_object_type=ContentType.objects.get_for_model(Device),
            assigned_object_id=self.devices[1].pk,
            status=self.assignment_status,
        )
        form = forms.OverlayAssignmentForm(instance=assignment)
        self.assertEqual(form.initial["object_type"], "device")
        self.assertEqual(form.initial["device"], self.devices[1])
        self.assertNotIn("interface", form.initial)

    def test_form_save_sets_generic_fk_for_device(self):
        """Saving with a device sets assigned_object_type and assigned_object_id correctly."""
        form = forms.OverlayAssignmentForm(
            data={
                "overlay": self.overlays[0].pk,
                "object_type": "device",
                "device": self.devices[2].pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        self.assertEqual(instance.assigned_object_type, ContentType.objects.get_for_model(Device))
        self.assertEqual(instance.assigned_object_id, self.devices[2].pk)
        instance.delete()


class IBPKeyOverlayAssignmentFormTestCase(TestCase):
    """Tests for IBPKeyOverlayAssignmentForm — IB PKey type-aware assignment form."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)
        create_device_test_data(cls)
        cls.ib_pkey_overlay = cls.overlays[2]  # isolation_type=IB_PKEY
        cls.interface = _create_interface(cls.devices[0], ib_guid="0002c903000e0b72")
        cls.interface_no_guid = _create_interface(cls.devices[0], name="eth1")

    def test_form_valid_with_interface_guid(self):
        """Valid IB PKey assignment form when interface has cf_ib_guid set."""
        form = forms.IBPKeyOverlayAssignmentForm(
            data={
                "overlay": self.ib_pkey_overlay.pk,
                "interface": self.interface.pk,
                "membership_type": PKeyMembershipTypeChoices.FULL,
                "role": OverlayAssignmentRoleChoices.COMPUTE,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_save_auto_populates_guid_from_interface(self):
        """Saving the form populates GUID from the interface's cf_ib_guid."""
        form = forms.IBPKeyOverlayAssignmentForm(
            data={
                "overlay": self.ib_pkey_overlay.pk,
                "interface": self.interface.pk,
                "membership_type": PKeyMembershipTypeChoices.FULL,
                "role": OverlayAssignmentRoleChoices.COMPUTE,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        self.assertEqual(instance.guid, "0002c903000e0b72")
        instance.delete()

    def test_form_invalid_when_interface_has_no_guid(self):
        """Form is invalid when the interface has no cf_ib_guid custom field."""
        form = forms.IBPKeyOverlayAssignmentForm(
            data={
                "overlay": self.ib_pkey_overlay.pk,
                "interface": self.interface_no_guid.pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cf_ib_guid", str(form.errors))

    def test_form_missing_interface_is_invalid(self):
        """IB PKey form rejects submission when interface is absent."""
        form = forms.IBPKeyOverlayAssignmentForm(
            data={
                "overlay": self.ib_pkey_overlay.pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("interface", form.errors)

    def test_form_has_no_rack_field_and_guid_is_hidden(self):
        """Rack is absent, guid is a hidden auto-populated field."""
        form = forms.IBPKeyOverlayAssignmentForm()
        self.assertNotIn("rack", form.fields)
        self.assertIsInstance(form.fields["guid"].widget, forms.forms.HiddenInput)

    def test_form_save_sets_generic_fk(self):
        """Saving the form sets assigned_object_type and assigned_object_id correctly."""
        form = forms.IBPKeyOverlayAssignmentForm(
            data={
                "overlay": self.ib_pkey_overlay.pk,
                "interface": self.interface.pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        interface_ct = ContentType.objects.get_for_model(Interface)
        self.assertEqual(instance.assigned_object_type, interface_ct)
        self.assertEqual(instance.assigned_object_id, self.interface.pk)
        self.assertEqual(instance.guid, "0002c903000e0b72")
        instance.delete()


class GeneralOverlayAssignmentFormTestCase(TestCase):
    """Tests for GeneralOverlayAssignmentForm — used for non-IB-PKey overlay types."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)
        create_device_test_data(cls)
        cls.vxlan_overlay = cls.overlays[0]  # isolation_type=VXLAN_EVPN
        cls.interface = _create_interface(cls.devices[0])

    def test_form_valid_with_device(self):
        """General assignment form is valid when a device is provided."""
        form = forms.GeneralOverlayAssignmentForm(
            data={
                "overlay": self.vxlan_overlay.pk,
                "object_type": "device",
                "device": self.devices[0].pk,
                "role": OverlayAssignmentRoleChoices.COMPUTE,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_valid_with_interface(self):
        """General assignment form is valid when an interface is provided."""
        form = forms.GeneralOverlayAssignmentForm(
            data={
                "overlay": self.vxlan_overlay.pk,
                "object_type": "interface",
                "interface": self.interface.pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_missing_object_type_is_invalid(self):
        """General assignment form rejects submission with no object_type."""
        form = forms.GeneralOverlayAssignmentForm(
            data={
                "overlay": self.vxlan_overlay.pk,
                "device": self.devices[0].pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())

    def test_form_object_type_device_without_device_is_invalid(self):
        """Selecting device type without picking a device produces an error."""
        form = forms.GeneralOverlayAssignmentForm(
            data={
                "overlay": self.vxlan_overlay.pk,
                "object_type": "device",
                "status": self.assignment_status.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("device", form.errors)

    def test_form_has_no_guid_or_membership_type_fields(self):
        """GUID and membership_type are not present in the general form."""
        form = forms.GeneralOverlayAssignmentForm()
        self.assertNotIn("guid", form.fields)
        self.assertNotIn("membership_type", form.fields)

    def test_form_save_sets_generic_fk_for_device(self):
        """Saving with a device sets assigned_object_type and assigned_object_id correctly."""
        form = forms.GeneralOverlayAssignmentForm(
            data={
                "overlay": self.vxlan_overlay.pk,
                "object_type": "device",
                "device": self.devices[0].pk,
                "status": self.assignment_status.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        device_ct = ContentType.objects.get_for_model(Device)
        self.assertEqual(instance.assigned_object_type, device_ct)
        self.assertEqual(instance.assigned_object_id, self.devices[0].pk)
        instance.delete()


class BulkAllocationFormTestCase(TestCase):
    """Tests for BulkAllocationForm validation."""

    @classmethod
    def setUpTestData(cls):
        create_overlay_test_data(cls)
        create_device_test_data(cls)

    def test_form_valid_with_devices(self):
        """Form is valid when devices are selected."""
        form = forms.BulkAllocationForm(
            data={
                "devices": [self.devices[0].pk, self.devices[1].pk],
                "interfaces": [],
                "racks": [],
                "role": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_empty_selection_invalid(self):
        """Form rejects submission with no objects selected."""
        form = forms.BulkAllocationForm(data={"devices": [], "interfaces": [], "racks": [], "role": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)
