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

"""Forms for Overlays app."""

import logging

from django import forms
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.forms import (
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    NautobotBulkEditForm,
    NautobotFilterForm,
    NautobotModelForm,
    StatusModelFilterFormMixin,
    TagFilterField,
)
from nautobot.dcim.models import Device, Interface, Location, Rack
from nautobot.extras.models import Status
from nautobot.ipam.models import VLAN, VRF, Namespace, RouteTarget
from nautobot.tenancy.models import Tenant

from nautobot_app_overlays import models
from nautobot_app_overlays.choices import (
    IsolationTypeChoices,
    OverlayAssignmentRoleChoices,
    PKeyMembershipTypeChoices,
    VNITypeChoices,
)

logger = logging.getLogger(__name__)

# Overlay isolation types whose assignments must not carry IB PKey attributes (GUID, membership type).
_GUIDLESS_ISOLATION_TYPES = (
    IsolationTypeChoices.VXLAN_EVPN,
    IsolationTypeChoices.SPECTRUM_X_VRF,
    IsolationTypeChoices.IB_MKEY,
)

# OverlayAssignmentForm object types, each backed by a same-named picker field, and the
# error raised when that picker is left empty.
_MISSING_OBJECT_MESSAGES = {
    "device": "Please select a device.",
    "interface": "Please select an interface.",
    "rack": "Please select a rack.",
    "vxlan": "Please select a VXLAN.",
}

# -----------------------------------------------------------------------------
# Overlay Forms
# -----------------------------------------------------------------------------


class OverlayForm(NautobotModelForm):
    """Form for creating/editing Overlay objects."""

    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all())

    class Meta:
        """Meta class."""

        model = models.Overlay
        fields = [
            "name",
            "description",
            "tenant",
            "location",
            "isolation_type",
            "status",
            "tags",
        ]


class VXLANOverlayForm(OverlayForm):
    """Form for VXLAN/EVPN overlay type."""

    isolation_type = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        """Force isolation_type initial value after ModelForm populates from instance."""
        super().__init__(*args, **kwargs)
        self.initial["isolation_type"] = IsolationTypeChoices.VXLAN_EVPN


class SpectrumXOverlayForm(OverlayForm):
    """Form for Spectrum X overlay type."""

    isolation_type = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        """Force isolation_type initial value after ModelForm populates from instance."""
        super().__init__(*args, **kwargs)
        self.initial["isolation_type"] = IsolationTypeChoices.SPECTRUM_X_VRF


class NVLinkPartitionOverlayForm(OverlayForm):
    """Form for NVLink Partition overlay type."""

    isolation_type = forms.CharField(widget=forms.HiddenInput())

    class Meta(OverlayForm.Meta):
        """Meta class."""

        fields = OverlayForm.Meta.fields + ["partition_id"]

    def __init__(self, *args, **kwargs):
        """Force isolation_type initial value after ModelForm populates from instance."""
        super().__init__(*args, **kwargs)
        self.initial["isolation_type"] = IsolationTypeChoices.NVLINK_PARTITION


class IBPKeyOverlayForm(OverlayForm):
    """Form for IB PKey overlay type."""

    isolation_type = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        """Force isolation_type initial value after ModelForm populates from instance."""
        super().__init__(*args, **kwargs)
        self.initial["isolation_type"] = IsolationTypeChoices.IB_PKEY

    def clean(self):
        """Log validation errors to aid debugging."""
        cleaned = super().clean()
        if self.errors:
            logger.warning("IBPKeyOverlayForm validation errors: %s", dict(self.errors))
            logger.warning("IBPKeyOverlayForm POST data keys: %s", list(self.data.keys()))
        return cleaned


class IBMKeyOverlayForm(OverlayForm):
    """Form for IB MKey overlay type."""

    isolation_type = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        """Force isolation_type initial value after ModelForm populates from instance."""
        super().__init__(*args, **kwargs)
        self.initial["isolation_type"] = IsolationTypeChoices.IB_MKEY


class OverlayBulkEditForm(NautobotBulkEditForm):
    """Form for bulk editing Overlay objects."""

    pk = forms.ModelMultipleChoiceField(queryset=models.Overlay.objects.all(), widget=forms.MultipleHiddenInput)
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)
    isolation_type = forms.ChoiceField(choices=IsolationTypeChoices, required=False)

    class Meta:
        """Meta class."""

        nullable_fields = ["description"]


class OverlayFilterForm(NautobotFilterForm, StatusModelFilterFormMixin):
    """Filter form for Overlay objects."""

    model = models.Overlay
    q = forms.CharField(required=False, label="Search")
    tenant = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False)
    location = DynamicModelMultipleChoiceField(queryset=Location.objects.all(), required=False)
    isolation_type = forms.MultipleChoiceField(choices=IsolationTypeChoices, required=False)
    tags = TagFilterField(model)
    field_order = ["q", "tenant", "location", "isolation_type", "status", "tags"]


class _TypedOverlayFilterForm(OverlayFilterForm):
    """Base filter form for type-specific overlay lists. Removes the isolation_type field."""

    def __init__(self, *args, **kwargs):
        """Remove isolation_type since the queryset is already pre-filtered."""
        super().__init__(*args, **kwargs)
        self.fields.pop("isolation_type", None)


class VXLANOverlayFilterForm(_TypedOverlayFilterForm):
    """Filter form for VXLAN overlay list."""


class SpectrumXOverlayFilterForm(_TypedOverlayFilterForm):
    """Filter form for Spectrum X overlay list."""


class NVLinkPartitionOverlayFilterForm(_TypedOverlayFilterForm):
    """Filter form for NVLink Partition overlay list."""


class IBPKeyOverlayFilterForm(_TypedOverlayFilterForm):
    """Filter form for IB PKey overlay list."""


class IBMKeyOverlayFilterForm(_TypedOverlayFilterForm):
    """Filter form for IB MKey overlay list."""


# -----------------------------------------------------------------------------
# OverlayAssignment Forms
# -----------------------------------------------------------------------------


class OverlayAssignmentForm(NautobotModelForm):
    """Form for creating/editing OverlayAssignment objects."""

    overlay = DynamicModelChoiceField(queryset=models.Overlay.objects.all())
    status = forms.ModelChoiceField(
        queryset=Status.objects.none(),  # Will be set in __init__
        required=True,
        label="Status",
    )

    # Object type selector - choices will be filtered based on overlay type
    object_type = forms.ChoiceField(
        choices=[
            ("", "---------"),
            ("device", "Device"),
            ("interface", "Interface"),
            ("rack", "Rack"),
            ("vxlan", "VXLAN"),
        ],
        required=True,
        label="Object Type",
        help_text="Type of object to assign",
    )

    # Searchable fields for each object type
    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    interface = DynamicModelChoiceField(
        queryset=Interface.objects.all(),
        required=False,
        label="Interface",
        query_params={"device_id": "$device"},
    )
    rack = DynamicModelChoiceField(
        queryset=Rack.objects.all(),
        required=False,
        label="Rack",
    )
    vxlan = DynamicModelChoiceField(
        queryset=models.VXLAN.objects.all(),
        required=False,
        label="VXLAN",
    )
    import_targets = DynamicModelMultipleChoiceField(
        queryset=RouteTarget.objects.all(),
        required=False,
    )
    export_targets = DynamicModelMultipleChoiceField(
        queryset=RouteTarget.objects.all(),
        required=False,
    )

    class Meta:
        """Meta class."""

        model = models.OverlayAssignment
        fields = [
            "overlay",
            "role",
            "guid",
            "membership_type",
            "import_targets",
            "export_targets",
            "status",
        ]

    def __init__(self, *args, **kwargs):
        """Initialize the form."""
        super().__init__(*args, **kwargs)

        # Set up the status queryset for OverlayAssignment
        self.fields["status"].queryset = Status.objects.get_for_model(models.OverlayAssignment)

        # If editing an existing assignment, populate the object type and object fields
        if self.instance and self.instance.pk:
            self._set_assigned_object_initial(self.instance.assigned_object)

    def _set_assigned_object_initial(self, assigned_obj):
        """Pre-select the object type and object picker for an existing assignment."""
        if not assigned_obj:
            return
        model_name = assigned_obj._meta.model_name
        self.initial["object_type"] = model_name
        if model_name not in _MISSING_OBJECT_MESSAGES:
            return
        self.initial[model_name] = assigned_obj
        if model_name == "interface" and getattr(assigned_obj, "device", None):
            self.initial["device"] = assigned_obj.device

    def clean(self):
        """Validate that the selected object matches the object type and isolation constraints."""
        cleaned_data = super().clean()

        if cleaned_data is None:
            return cleaned_data

        object_type = cleaned_data.get("object_type")
        overlay = cleaned_data.get("overlay")

        if not object_type:
            raise forms.ValidationError("Please select an object type.")

        # Validate object type against overlay isolation type
        if overlay:
            self._validate_isolation_constraints(overlay.isolation_type, object_type, cleaned_data)

        cleaned_data["_selected_object"] = self._get_selected_object(object_type, cleaned_data)
        return cleaned_data

    def _validate_isolation_constraints(self, isolation_type, object_type, cleaned_data):
        """Enforce the member rules implied by the overlay's isolation type."""
        if isolation_type == IsolationTypeChoices.IB_PKEY:
            if object_type != "interface":
                raise forms.ValidationError(
                    "IB PKey overlays can only have Interface assignments. Please select an interface."
                )
            if not cleaned_data.get("guid"):
                self.add_error("guid", "GUID is required for IB PKey overlay assignments.")
            return

        if isolation_type not in _GUIDLESS_ISOLATION_TYPES:
            return
        for field_name, label in (("guid", "GUID"), ("membership_type", "Membership type")):
            if cleaned_data.get(field_name):
                self.add_error(field_name, f"{label} should not be set for {isolation_type} overlay assignments.")

    @staticmethod
    def _get_selected_object(object_type, cleaned_data):
        """Return the object chosen for ``object_type``, raising if the picker was left empty."""
        missing_message = _MISSING_OBJECT_MESSAGES.get(object_type)
        if missing_message is None:
            return None
        selected_object = cleaned_data.get(object_type)
        if not selected_object:
            raise forms.ValidationError(missing_message)
        return selected_object

    def save(self, commit=True):
        """Save the form, setting the GenericForeignKey fields."""
        instance = super().save(commit=False)

        selected_object = self.cleaned_data.get("_selected_object")

        if not selected_object:
            object_type = self.cleaned_data.get("object_type")
            if object_type in _MISSING_OBJECT_MESSAGES:
                selected_object = self.cleaned_data.get(object_type)

        if not selected_object:
            raise forms.ValidationError("No object selected. Please select a device, interface, or rack.")

        instance.assigned_object_type = ContentType.objects.get_for_model(selected_object)
        instance.assigned_object_id = selected_object.pk

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class IBPKeyOverlayAssignmentForm(NautobotModelForm):
    """Form for creating an OverlayAssignment on an IB PKey overlay."""

    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.filter(isolation_type=IsolationTypeChoices.IB_PKEY),
        widget=forms.HiddenInput(),
    )
    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    interface = DynamicModelChoiceField(
        queryset=Interface.objects.all(),
        required=True,
        label="Interface",
        query_params={"device_id": "$device"},
    )
    status = forms.ModelChoiceField(
        queryset=Status.objects.none(),
        required=True,
        label="Status",
    )

    class Meta:
        """Meta class."""

        model = models.OverlayAssignment
        fields = ["overlay", "guid", "membership_type", "role", "status"]

    def __init__(self, *args, **kwargs):
        """Initialize: hide guid and scope status queryset."""
        super().__init__(*args, **kwargs)
        self.fields["guid"].widget = forms.HiddenInput()
        self.fields["guid"].required = False
        self.fields["status"].queryset = Status.objects.get_for_model(models.OverlayAssignment)

    def clean(self):
        """Resolve GUID from the interface's cf_ib_guid before model validation."""
        super().clean()
        interface = self.cleaned_data.get("interface")
        if interface:
            guid = (interface.cf or {}).get("ib_guid", "")
            if not guid:
                raise forms.ValidationError(
                    f"Interface '{interface}' has no IB GUID (cf_ib_guid) set. "
                    "Populate the custom field before creating a PKey assignment."
                )
            self.cleaned_data["guid"] = guid
            self.instance.guid = guid
            self.instance.assigned_object_type = ContentType.objects.get_for_model(Interface)
            self.instance.assigned_object_id = interface.pk
        return self.cleaned_data

    def save(self, commit=True):
        """Persist the instance with the GenericForeignKey already set by clean()."""
        instance = super().save(commit=False)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class GeneralOverlayAssignmentForm(NautobotModelForm):
    """Form for creating an OverlayAssignment on non-IB-PKey overlays."""

    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        widget=forms.HiddenInput(),
    )
    object_type = forms.ChoiceField(
        choices=[
            ("", "---------"),
            ("device", "Device"),
            ("interface", "Interface"),
            ("rack", "Rack"),
            ("vxlan", "VXLAN"),
        ],
        required=True,
        label="Object Type",
    )
    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    interface = DynamicModelChoiceField(
        queryset=Interface.objects.all(),
        required=False,
        label="Interface",
        query_params={"device_id": "$device"},
    )
    rack = DynamicModelChoiceField(
        queryset=Rack.objects.all(),
        required=False,
        label="Rack",
    )
    vxlan = DynamicModelChoiceField(
        queryset=models.VXLAN.objects.all(),
        required=False,
        label="VXLAN",
    )
    import_targets = DynamicModelMultipleChoiceField(
        queryset=RouteTarget.objects.all(),
        required=False,
    )
    export_targets = DynamicModelMultipleChoiceField(
        queryset=RouteTarget.objects.all(),
        required=False,
    )
    status = forms.ModelChoiceField(
        queryset=Status.objects.none(),
        required=True,
        label="Status",
    )

    class Meta:
        """Meta class."""

        model = models.OverlayAssignment
        fields = ["overlay", "role", "import_targets", "export_targets", "status"]

    def __init__(self, *args, **kwargs):
        """Initialize: scope status queryset and make the selected object field required."""
        super().__init__(*args, **kwargs)
        self.fields["status"].queryset = Status.objects.get_for_model(models.OverlayAssignment)
        submitted_type = self.data.get("object_type")
        if submitted_type in ("device", "interface", "rack", "vxlan"):
            self.fields[submitted_type].required = True

    def clean(self):
        """Store the selected object for use in save()."""
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data
        object_type = cleaned_data.get("object_type")
        if object_type in ("device", "interface", "rack", "vxlan"):
            cleaned_data["_selected_object"] = cleaned_data.get(object_type)
        return cleaned_data

    def save(self, commit=True):
        """Set GenericForeignKey from the selected object."""
        instance = super().save(commit=False)
        selected_object = self.cleaned_data.get("_selected_object") or self.cleaned_data.get(
            self.cleaned_data.get("object_type")
        )
        instance.assigned_object_type = ContentType.objects.get_for_model(selected_object)
        instance.assigned_object_id = selected_object.pk
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class OverlayAssignmentBulkEditForm(NautobotBulkEditForm):
    """Form for bulk editing OverlayAssignment objects."""

    pk = forms.ModelMultipleChoiceField(
        queryset=models.OverlayAssignment.objects.all(), widget=forms.MultipleHiddenInput
    )
    role = forms.ChoiceField(choices=OverlayAssignmentRoleChoices, required=False)
    membership_type = forms.ChoiceField(choices=PKeyMembershipTypeChoices, required=False)
    import_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    export_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)

    class Meta:
        """Meta class."""

        nullable_fields = ["role", "guid", "membership_type", "import_targets", "export_targets"]


class OverlayAssignmentFilterForm(NautobotFilterForm, StatusModelFilterFormMixin):
    """Filter form for OverlayAssignment objects."""

    model = models.OverlayAssignment
    q = forms.CharField(required=False, label="Search")
    overlay = DynamicModelMultipleChoiceField(queryset=models.Overlay.objects.all(), required=False)
    role = forms.MultipleChoiceField(choices=OverlayAssignmentRoleChoices, required=False)
    membership_type = forms.MultipleChoiceField(choices=PKeyMembershipTypeChoices, required=False)
    guid = forms.CharField(required=False, label="GUID")
    import_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    export_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    field_order = ["q", "overlay", "role", "membership_type", "guid", "import_targets", "export_targets", "status"]


# -----------------------------------------------------------------------------
# VXLAN Forms
# -----------------------------------------------------------------------------


class VXLANForm(NautobotModelForm):
    """Form for creating/editing VXLAN objects."""

    vnid = forms.IntegerField(label="VNID")
    vni_type = forms.ChoiceField(
        choices=VNITypeChoices,
        label="VNI Type",
        help_text="L2 VNI for VLAN extension, L3 VNI for VRF routing.",
    )
    l3_vlan_id = forms.IntegerField(
        required=False,
        label="L3 VLAN ID",
        help_text="Local VLAN ID for L3 VNI SVI.",
        min_value=1,
        max_value=4094,
    )
    namespace = DynamicModelChoiceField(queryset=Namespace.objects.all())
    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        query_params={"isolation_type": IsolationTypeChoices.VXLAN_EVPN},
        required=False,
    )
    vlan = DynamicModelChoiceField(
        queryset=VLAN.objects.all(),
        required=False,
        label="VLAN",
    )
    vrf = DynamicModelChoiceField(
        queryset=VRF.objects.all(),
        required=False,
        label="VRF",
    )
    import_targets = DynamicModelMultipleChoiceField(
        queryset=RouteTarget.objects.all(),
        required=False,
    )
    export_targets = DynamicModelMultipleChoiceField(
        queryset=RouteTarget.objects.all(),
        required=False,
    )
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)

    class Meta:
        """Meta class."""

        model = models.VXLAN
        fields = [
            "name",
            "vnid",
            "vni_type",
            "namespace",
            "status",
            "vlan",
            "vrf",
            "import_targets",
            "export_targets",
            "l3_vlan_id",
            "tenant",
            "tags",
        ]


class VXLANBulkEditForm(NautobotBulkEditForm):
    """Form for bulk editing VXLAN objects."""

    pk = forms.ModelMultipleChoiceField(queryset=models.VXLAN.objects.all(), widget=forms.MultipleHiddenInput)
    vni_type = forms.ChoiceField(choices=VNITypeChoices, required=False, label="VNI Type")
    l3_vlan_id = forms.IntegerField(required=False, label="L3 VLAN ID", min_value=1, max_value=4094)
    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        query_params={"isolation_type": IsolationTypeChoices.VXLAN_EVPN},
        required=False,
    )
    import_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    export_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)

    class Meta:
        """Meta class."""

        nullable_fields = ["overlay", "vlan", "vrf", "tenant", "l3_vlan_id", "import_targets", "export_targets"]


class VXLANFilterForm(NautobotFilterForm, StatusModelFilterFormMixin):
    """Filter form for VXLAN objects."""

    model = models.VXLAN
    q = forms.CharField(required=False, label="Search")
    vni_type = forms.MultipleChoiceField(choices=VNITypeChoices, required=False, label="VNI Type")
    namespace = DynamicModelMultipleChoiceField(queryset=Namespace.objects.all(), required=False)
    overlay = DynamicModelMultipleChoiceField(queryset=models.Overlay.objects.all(), required=False)
    tenant = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False)
    vnid__gte = forms.IntegerField(required=False, label="VNID (min)")
    vnid__lte = forms.IntegerField(required=False, label="VNID (max)")
    import_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    export_targets = DynamicModelMultipleChoiceField(queryset=RouteTarget.objects.all(), required=False)
    tags = TagFilterField(model)
    field_order = [
        "q",
        "vni_type",
        "namespace",
        "overlay",
        "tenant",
        "import_targets",
        "export_targets",
        "vnid__gte",
        "vnid__lte",
        "status",
        "tags",
    ]


# -----------------------------------------------------------------------------
# InfiniBandPKey Forms
# -----------------------------------------------------------------------------


class InfiniBandPKeyForm(NautobotModelForm):
    """Form for creating/editing InfiniBandPKey objects."""

    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        query_params={"isolation_type": IsolationTypeChoices.IB_PKEY},
        required=False,
    )
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)

    class Meta:
        """Meta class."""

        model = models.InfiniBandPKey
        fields = [
            "name",
            "pkey",
            "overlay",
            "membership_type",
            "status",
            "tenant",
            "qos_config",
            "tags",
        ]


class InfiniBandPKeyBulkEditForm(NautobotBulkEditForm):
    """Form for bulk editing InfiniBandPKey objects."""

    pk = forms.ModelMultipleChoiceField(queryset=models.InfiniBandPKey.objects.all(), widget=forms.MultipleHiddenInput)
    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        query_params={"isolation_type": IsolationTypeChoices.IB_PKEY},
        required=False,
    )
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)
    membership_type = forms.ChoiceField(choices=PKeyMembershipTypeChoices, required=False)

    class Meta:
        """Meta class."""

        nullable_fields = ["overlay", "tenant", "qos_config"]


class InfiniBandPKeyFilterForm(NautobotFilterForm, StatusModelFilterFormMixin):
    """Filter form for InfiniBandPKey objects."""

    model = models.InfiniBandPKey
    q = forms.CharField(required=False, label="Search")
    overlay = DynamicModelMultipleChoiceField(queryset=models.Overlay.objects.all(), required=False)
    tenant = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False)
    membership_type = forms.MultipleChoiceField(choices=PKeyMembershipTypeChoices, required=False)
    tags = TagFilterField(model)
    field_order = ["q", "overlay", "tenant", "membership_type", "status", "tags"]


# -----------------------------------------------------------------------------
# InfiniBandMKey Forms
# -----------------------------------------------------------------------------


class InfiniBandMKeyForm(NautobotModelForm):
    """Form for creating/editing InfiniBandMKey objects."""

    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        query_params={"isolation_type": IsolationTypeChoices.IB_MKEY},
        required=False,
    )
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)
    ufm_device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="UFM Device",
        help_text="UFM server device",
    )

    class Meta:
        """Meta class."""

        model = models.InfiniBandMKey
        fields = [
            "name",
            "mkey_value",
            "overlay",
            "ufm_device",
            "status",
            "tenant",
            "protect_bits",
            "mkey_lease_period",
            "mkey_per_port",
            "mkey_global_seed",
            "tags",
        ]


class InfiniBandMKeyBulkEditForm(NautobotBulkEditForm):
    """Form for bulk editing InfiniBandMKey objects."""

    pk = forms.ModelMultipleChoiceField(queryset=models.InfiniBandMKey.objects.all(), widget=forms.MultipleHiddenInput)
    overlay = DynamicModelChoiceField(
        queryset=models.Overlay.objects.all(),
        query_params={"isolation_type": IsolationTypeChoices.IB_MKEY},
        required=False,
    )
    tenant = DynamicModelChoiceField(queryset=Tenant.objects.all(), required=False)
    ufm_device = DynamicModelChoiceField(queryset=Device.objects.all(), required=False, label="UFM Device")
    mkey_per_port = forms.NullBooleanField(required=False, label="MKey Per Port")

    class Meta:
        """Meta class."""

        nullable_fields = ["overlay", "tenant", "ufm_device", "mkey_global_seed"]


class InfiniBandMKeyFilterForm(NautobotFilterForm, StatusModelFilterFormMixin):
    """Filter form for InfiniBandMKey objects."""

    model = models.InfiniBandMKey
    q = forms.CharField(required=False, label="Search")
    overlay = DynamicModelMultipleChoiceField(queryset=models.Overlay.objects.all(), required=False)
    tenant = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False)
    ufm_device = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False, label="UFM Device")
    mkey_per_port = forms.NullBooleanField(required=False, label="MKey Per Port")
    tags = TagFilterField(model)
    field_order = ["q", "overlay", "tenant", "ufm_device", "mkey_per_port", "status", "tags"]


# -----------------------------------------------------------------------------
# Bulk Allocation Form
# -----------------------------------------------------------------------------


class BulkAllocationForm(forms.Form):
    """Form for bulk allocating objects to an overlay."""

    devices = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Devices",
    )
    interfaces = DynamicModelMultipleChoiceField(
        queryset=Interface.objects.all(),
        required=False,
        label="Interfaces",
        query_params={"device_id": "$devices"},
    )
    racks = DynamicModelMultipleChoiceField(
        queryset=Rack.objects.all(),
        required=False,
        label="Racks",
    )
    role = forms.ChoiceField(
        choices=[("", "---------")] + list(OverlayAssignmentRoleChoices.CHOICES),
        required=False,
        label="Role",
    )

    def clean(self):
        """Validate that at least one object is selected."""
        cleaned_data = super().clean()
        devices = cleaned_data.get("devices", [])
        interfaces = cleaned_data.get("interfaces", [])
        racks = cleaned_data.get("racks", [])

        if not devices and not interfaces and not racks:
            raise forms.ValidationError("Please select at least one device, interface, or rack to allocate.")

        return cleaned_data
