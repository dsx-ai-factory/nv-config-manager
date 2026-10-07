#  SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for OverlayAssignmentSerializer isolation-type validation.

``NautobotModelSerializer.validate`` is patched to a pass-through so these tests
exercise only the serializer's own isolation rules, independent of model ``clean()``.
"""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from nautobot.apps.api import NautobotModelSerializer
from rest_framework import serializers

from nautobot_app_overlays.api.serializers import OverlayAssignmentSerializer
from nautobot_app_overlays.choices import IsolationTypeChoices, PKeyMembershipTypeChoices

GUID = "0002c903000e0b72"


def _overlay(isolation_type):
    return SimpleNamespace(isolation_type=isolation_type)


def _content_type(model):
    return SimpleNamespace(model=model)


@mock.patch.object(NautobotModelSerializer, "validate", lambda self, data: data)
class OverlayAssignmentSerializerValidateTestCase(SimpleTestCase):
    """Tests for OverlayAssignmentSerializer.validate."""

    def _validate(self, data, instance=None):
        return OverlayAssignmentSerializer(instance=instance).validate(data)

    def _validation_errors(self, data, instance=None):
        with self.assertRaises(serializers.ValidationError) as ctx:
            self._validate(data, instance=instance)
        return ctx.exception.detail

    def test_no_overlay_skips_validation(self):
        data = {"guid": GUID, "membership_type": PKeyMembershipTypeChoices.FULL}
        self.assertEqual(self._validate(data), data)

    def test_ib_pkey_interface_with_guid_is_valid(self):
        data = {
            "overlay": _overlay(IsolationTypeChoices.IB_PKEY),
            "assigned_object_type": _content_type("interface"),
            "guid": GUID,
        }
        self.assertEqual(self._validate(data), data)

    def test_ib_pkey_rejects_non_interface_and_missing_guid(self):
        errors = self._validation_errors(
            {
                "overlay": _overlay(IsolationTypeChoices.IB_PKEY),
                "assigned_object_type": _content_type("device"),
            }
        )
        self.assertEqual(set(errors), {"assigned_object_type", "guid"})
        self.assertIn("only have Interface members", str(errors["assigned_object_type"]))
        self.assertIn("GUID is required", str(errors["guid"]))

    def test_ib_pkey_partial_update_falls_back_to_instance(self):
        instance = SimpleNamespace(
            overlay=_overlay(IsolationTypeChoices.IB_PKEY),
            assigned_object_type=_content_type("interface"),
            guid=GUID,
            membership_type="",
        )
        data = {"role": "compute"}
        self.assertEqual(self._validate(data, instance=instance), data)

    def test_ib_pkey_explicit_blank_guid_overrides_instance(self):
        instance = SimpleNamespace(
            overlay=_overlay(IsolationTypeChoices.IB_PKEY),
            assigned_object_type=_content_type("interface"),
            guid=GUID,
            membership_type="",
        )
        errors = self._validation_errors({"guid": ""}, instance=instance)
        self.assertEqual(set(errors), {"guid"})

    def test_guidless_types_reject_guid_and_membership_type(self):
        for isolation_type in (
            IsolationTypeChoices.VXLAN_EVPN,
            IsolationTypeChoices.SPECTRUM_X_VRF,
            IsolationTypeChoices.IB_MKEY,
        ):
            with self.subTest(isolation_type=isolation_type):
                errors = self._validation_errors(
                    {
                        "overlay": _overlay(isolation_type),
                        "assigned_object_type": _content_type("device"),
                        "guid": GUID,
                        "membership_type": PKeyMembershipTypeChoices.FULL,
                    }
                )
                self.assertEqual(set(errors), {"guid", "membership_type"})
                self.assertIn(f"GUID should not be set for {isolation_type}", str(errors["guid"]))

    def test_guidless_type_without_ib_fields_is_valid(self):
        data = {
            "overlay": _overlay(IsolationTypeChoices.VXLAN_EVPN),
            "assigned_object_type": _content_type("device"),
        }
        self.assertEqual(self._validate(data), data)

    def test_nvlink_partition_has_no_member_constraints(self):
        data = {
            "overlay": _overlay(IsolationTypeChoices.NVLINK_PARTITION),
            "assigned_object_type": _content_type("device"),
            "guid": GUID,
            "membership_type": PKeyMembershipTypeChoices.FULL,
        }
        self.assertEqual(self._validate(data), data)
