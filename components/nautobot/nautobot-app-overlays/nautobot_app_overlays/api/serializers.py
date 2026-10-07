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

"""Serializers for Overlays API."""

from django.contrib.contenttypes.models import ContentType
from nautobot.apps.api import NautobotModelSerializer
from nautobot.core.api import ContentTypeField
from rest_framework import serializers

from nautobot_app_overlays import models
from nautobot_app_overlays.choices import ASSIGNABLE_CONTENT_TYPES, IsolationTypeChoices

# Overlay isolation types whose members must not carry IB PKey attributes (GUID, membership type).
_GUIDLESS_ISOLATION_TYPES = (
    IsolationTypeChoices.VXLAN_EVPN,
    IsolationTypeChoices.SPECTRUM_X_VRF,
    IsolationTypeChoices.IB_MKEY,
)


class OverlaySerializer(NautobotModelSerializer):
    """Serializer for Overlay model."""

    member_count = serializers.SerializerMethodField()

    def get_member_count(self, obj):
        """Return assignment count from annotation or live query."""
        if "member_count" in obj.__dict__:
            return obj.__dict__["member_count"]
        return obj.assignments.count()

    class Meta:
        """Meta class."""

        model = models.Overlay
        fields = "__all__"


class OverlayAssignmentSerializer(NautobotModelSerializer):
    """Serializer for OverlayAssignment model."""

    assigned_object_type = ContentTypeField(
        queryset=ContentType.objects.filter(model__in=ASSIGNABLE_CONTENT_TYPES),
    )

    class Meta:
        """Meta class."""

        model = models.OverlayAssignment
        fields = "__all__"

    def validate(self, data):
        """Validate member constraints based on overlay's isolation type."""
        data = super().validate(data)

        overlay = data.get("overlay") or (self.instance.overlay if self.instance else None)
        if not overlay:
            return data

        isolation_type = overlay.isolation_type
        if isolation_type == IsolationTypeChoices.IB_PKEY:
            errors = self._ib_pkey_member_errors(data)
        elif isolation_type in _GUIDLESS_ISOLATION_TYPES:
            errors = self._guidless_member_errors(data, isolation_type)
        else:
            errors = {}

        if errors:
            raise serializers.ValidationError(errors)

        return data

    def _effective_value(self, data, field):
        """Return the submitted value for ``field``, falling back to the instance being updated."""
        return data.get(field, getattr(self.instance, field, None))

    def _ib_pkey_member_errors(self, data):
        """Return field errors for a member of an IB PKey overlay."""
        errors = {}
        assigned_object_type = data.get("assigned_object_type") or (
            self.instance.assigned_object_type if self.instance else None
        )
        object_model = assigned_object_type.model if assigned_object_type else None
        if object_model and object_model != "interface":
            errors["assigned_object_type"] = "IB PKey overlays can only have Interface members."
        if not self._effective_value(data, "guid"):
            errors["guid"] = "GUID is required for IB PKey overlay members."
        return errors

    def _guidless_member_errors(self, data, isolation_type):
        """Return field errors for a member of an overlay type that must not carry IB PKey attributes."""
        errors = {}
        if self._effective_value(data, "guid"):
            errors["guid"] = f"GUID should not be set for {isolation_type} overlay members."
        if self._effective_value(data, "membership_type"):
            errors["membership_type"] = f"Membership type should not be set for {isolation_type} overlay members."
        return errors


class VXLANSerializer(NautobotModelSerializer):
    """Serializer for VXLAN model."""

    class Meta:
        """Meta class."""

        model = models.VXLAN
        fields = "__all__"

    def validate(self, data):
        """Validate VXLAN can only be associated with VXLAN/EVPN overlays."""
        data = super().validate(data)
        overlay = data.get("overlay") or (self.instance.overlay if self.instance else None)

        if overlay and overlay.isolation_type not in (
            IsolationTypeChoices.VXLAN_EVPN,
            IsolationTypeChoices.SPECTRUM_X_VRF,
        ):
            raise serializers.ValidationError(
                {"overlay": "VXLANs can only be associated with VXLAN/EVPN or Spectrum X overlays."}
            )
        return data


class InfiniBandPKeySerializer(NautobotModelSerializer):
    """Serializer for InfiniBandPKey model."""

    class Meta:
        """Meta class."""

        model = models.InfiniBandPKey
        fields = "__all__"
        extra_kwargs = {
            "overlay": {"required": False, "allow_null": True},
        }

    def validate(self, data):
        """Validate PKey can only be associated with IB PKey overlays."""
        data = super().validate(data)
        overlay = data.get("overlay") or (self.instance.overlay if self.instance else None)

        if overlay and overlay.isolation_type != IsolationTypeChoices.IB_PKEY:
            raise serializers.ValidationError(
                {"overlay": "InfiniBand PKeys can only be associated with IB PKey overlays."}
            )
        return data


class InfiniBandMKeySerializer(NautobotModelSerializer):
    """Serializer for InfiniBandMKey model."""

    class Meta:
        """Meta class."""

        model = models.InfiniBandMKey
        fields = "__all__"
        extra_kwargs = {
            "overlay": {"required": False, "allow_null": True},
            "ufm_device": {"required": False, "allow_null": True},
        }

    def validate(self, data):
        """Validate MKey can only be associated with IB MKey overlays."""
        data = super().validate(data)
        overlay = data.get("overlay") or (self.instance.overlay if self.instance else None)

        if overlay and overlay.isolation_type != IsolationTypeChoices.IB_MKEY:
            raise serializers.ValidationError(
                {"overlay": "InfiniBand MKeys can only be associated with IB MKey overlays."}
            )
        return data


class BulkAllocationSerializer(serializers.Serializer):
    """Serializer for bulk allocation endpoint."""

    object_type = serializers.ChoiceField(choices=ASSIGNABLE_CONTENT_TYPES)
    object_ids = serializers.ListField(child=serializers.UUIDField())
    role = serializers.CharField(required=False, allow_blank=True)
    guid = serializers.CharField(required=False, allow_blank=True)

    def validate_object_type(self, value):
        """Resolve object_type string to a ContentType."""
        app_label_map = {
            "device": "dcim",
            "interface": "dcim",
            "rack": "dcim",
            "vrf": "ipam",
            "vlan": "ipam",
            "prefix": "ipam",
            "vxlan": "nautobot_app_overlays",
        }
        try:
            return ContentType.objects.get(
                app_label=app_label_map[value],
                model=value,
            )
        except ContentType.DoesNotExist:
            raise serializers.ValidationError(f"Invalid object type: {value}")

    def validate(self, data):
        """Validate object IDs exist and that GUID is provided for IB PKey overlays."""
        content_type = data["object_type"]
        model_class = content_type.model_class()
        object_ids = data["object_ids"]

        existing_ids = set(model_class.objects.filter(pk__in=object_ids).values_list("pk", flat=True))
        missing_ids = set(object_ids) - existing_ids
        if missing_ids:
            raise serializers.ValidationError(
                {"object_ids": f"Objects not found: {', '.join(str(pk) for pk in missing_ids)}"}
            )

        # Validate GUID requirement when overlay is IB PKey
        overlay = self.context.get("overlay")
        if overlay and overlay.isolation_type == IsolationTypeChoices.IB_PKEY:
            if not data.get("guid"):
                raise serializers.ValidationError({"guid": "GUID is required when allocating to an IB PKey overlay."})

        return data


class BulkDeallocationSerializer(serializers.Serializer):
    """Serializer for bulk deallocation endpoint."""

    member_ids = serializers.ListField(child=serializers.UUIDField())
