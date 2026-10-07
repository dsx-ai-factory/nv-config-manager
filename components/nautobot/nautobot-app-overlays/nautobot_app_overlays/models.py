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

"""Models for Overlays app."""

import re

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from nautobot.apps.models import OrganizationalModel, PrimaryModel, extras_features
from nautobot.core.constants import CHARFIELD_MAX_LENGTH
from nautobot.extras.models import StatusField

from nautobot_app_overlays.choices import (
    ASSIGNABLE_CONTENT_TYPES,
    GUIDLESS_ISOLATION_TYPES,
    IsolationTypeChoices,
    OverlayAssignmentRoleChoices,
    PKeyMembershipTypeChoices,
    VNITypeChoices,
)

GUID_MAX_LENGTH = 20
MKEY_HEX_VALIDATOR = RegexValidator(
    r"^0x[0-9a-fA-F]{1,16}$",
    message="Must be a 64-bit hex value (e.g., '0x0000000000a12c30')",
)


@extras_features("graphql", "statuses", "custom_fields", "relationships")
class Overlay(PrimaryModel):
    """Tenant network overlay scoped to a location."""

    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, help_text="Overlay name")
    description = models.TextField(blank=True, help_text="Optional description")
    tenant = models.ForeignKey(
        to="tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="overlays",
        help_text="Owning tenant",
    )
    location = models.ForeignKey(
        to="dcim.Location",
        on_delete=models.PROTECT,
        related_name="overlays",
        help_text="Superpod/site where overlay exists",
    )
    isolation_type = models.CharField(
        max_length=50,
        choices=IsolationTypeChoices,
        help_text="Type of network isolation",
    )
    partition_id = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(32766)],
        help_text="NVLink Partition identifier",
    )
    status = StatusField()

    class Meta:
        """Meta class."""

        ordering = ["name"]
        unique_together = ["name", "location"]
        verbose_name = "Overlay"
        verbose_name_plural = "Overlays"

    def __str__(self):
        """Stringify instance."""
        return self.name

    def clean(self):
        """Prevent changing isolation_type when assignments already exist."""
        super().clean()
        if not self.pk or not self.isolation_type:
            return
        try:
            original = Overlay.objects.get(pk=self.pk)
            if original.isolation_type != self.isolation_type and self.assignments.exists():
                raise ValidationError({"isolation_type": "Cannot change isolation type when assignments exist."})
        except Overlay.DoesNotExist:
            pass


@extras_features("graphql", "statuses", "custom_fields")
class OverlayAssignment(OrganizationalModel):
    """Associates a resource with an overlay."""

    overlay = models.ForeignKey(
        to=Overlay,
        on_delete=models.CASCADE,
        related_name="assignments",
        help_text="Parent overlay",
    )
    assigned_object_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.CASCADE,
        limit_choices_to={"model__in": ASSIGNABLE_CONTENT_TYPES},
        help_text="Type of assigned object",
    )
    assigned_object_id = models.UUIDField(help_text="ID of assigned object", db_index=True)
    assigned_object = GenericForeignKey("assigned_object_type", "assigned_object_id")
    role = models.CharField(
        max_length=50,
        choices=OverlayAssignmentRoleChoices,
        blank=True,
        help_text="Role of the assignment in the overlay",
    )
    guid = models.CharField(
        max_length=GUID_MAX_LENGTH,
        blank=True,
        help_text="InfiniBand port GUID",
    )
    membership_type = models.CharField(
        max_length=20,
        choices=PKeyMembershipTypeChoices,
        blank=True,
        help_text="PKey membership type override",
    )
    import_targets = models.ManyToManyField(
        to="ipam.RouteTarget",
        related_name="importing_overlay_assignments",
        blank=True,
        help_text="Import route targets",
    )
    export_targets = models.ManyToManyField(
        to="ipam.RouteTarget",
        related_name="exporting_overlay_assignments",
        blank=True,
        help_text="Export route targets",
    )
    status = StatusField()

    class Meta:
        """Meta class."""

        ordering = ["overlay", "assigned_object_type", "assigned_object_id"]
        unique_together = ["overlay", "assigned_object_type", "assigned_object_id"]
        verbose_name = "Overlay Assignment"
        verbose_name_plural = "Overlay Assignments"

    def __str__(self):
        """Stringify instance."""
        return f"{self.assigned_object} in {self.overlay}"

    def clean(self):
        """Validate assignment constraints based on overlay's isolation type."""
        super().clean()

        if not self.overlay_id:
            return

        isolation_type = self.overlay.isolation_type
        object_model = None
        if self.assigned_object_type_id:
            object_model = self.assigned_object_type.model

        if isolation_type == IsolationTypeChoices.IB_PKEY:
            if object_model and object_model != "interface":
                raise ValidationError({"assigned_object_type": "IB PKey overlays can only have Interface assignments."})
            if not self.guid:
                raise ValidationError({"guid": "GUID is required for IB PKey overlay assignments."})

        elif isolation_type in GUIDLESS_ISOLATION_TYPES:
            if self.guid:
                raise ValidationError({"guid": f"GUID should not be set for {isolation_type} overlay assignments."})
            if self.membership_type:
                raise ValidationError(
                    {"membership_type": f"Membership type should not be set for {isolation_type} overlay assignments."}
                )

    def get_effective_membership_type(self):
        """Return effective membership type, falling back to overlay's PKey default."""
        if self.membership_type:
            return self.membership_type
        pkey = self.overlay.pkeys.first()
        if pkey:
            return pkey.membership_type
        return PKeyMembershipTypeChoices.FULL


@extras_features("graphql", "statuses", "custom_fields")
class VXLAN(PrimaryModel):
    """VXLAN Network Identifier."""

    vnid = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(16777215)],
        help_text="24-bit VNI (1-16777215)",
    )
    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, help_text="Descriptive name")
    vni_type = models.CharField(
        max_length=10,
        choices=VNITypeChoices,
        default=VNITypeChoices.L2_VNI,
        help_text="L2 VNI for VLAN extension, L3 VNI for VRF routing",
    )
    l3_vlan_id = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(4094)],
        help_text="Local VLAN ID for L3 VNI SVI",
    )
    namespace = models.ForeignKey(
        to="ipam.Namespace",
        on_delete=models.PROTECT,
        related_name="vxlans",
        help_text="Scopes VNI uniqueness",
    )
    overlay = models.ForeignKey(
        to=Overlay,
        on_delete=models.SET_NULL,
        related_name="vxlans",
        blank=True,
        null=True,
        help_text="Associated overlay",
    )
    vlan = models.ForeignKey(
        to="ipam.VLAN",
        on_delete=models.SET_NULL,
        related_name="vxlans",
        blank=True,
        null=True,
        help_text="L2 VLAN mapping",
    )
    vrf = models.ForeignKey(
        to="ipam.VRF",
        on_delete=models.SET_NULL,
        related_name="vxlans",
        blank=True,
        null=True,
        help_text="L3 VRF mapping",
    )
    import_targets = models.ManyToManyField(
        to="ipam.RouteTarget",
        related_name="importing_vxlans",
        blank=True,
        help_text="Import route targets for this VNI.",
    )
    export_targets = models.ManyToManyField(
        to="ipam.RouteTarget",
        related_name="exporting_vxlans",
        blank=True,
        help_text="Export route targets for this VNI.",
    )
    tenant = models.ForeignKey(
        to="tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="vxlans",
        blank=True,
        null=True,
        help_text="Owning tenant",
    )
    status = StatusField()

    class Meta:
        """Meta class."""

        ordering = ["vnid"]
        unique_together = ["vnid", "namespace"]
        verbose_name = "VXLAN"
        verbose_name_plural = "VXLANs"

    def __str__(self):
        """Stringify instance."""
        return f"{self.name} (VNI: {self.vnid})"

    VXLAN_COMPATIBLE_ISOLATION_TYPES = (
        IsolationTypeChoices.VXLAN_EVPN,
        IsolationTypeChoices.SPECTRUM_X_VRF,
    )

    def clean(self):
        """Validate VXLAN can only be associated with VXLAN-based overlays."""
        super().clean()
        if self.overlay and self.overlay.isolation_type not in self.VXLAN_COMPATIBLE_ISOLATION_TYPES:
            raise ValidationError({"overlay": "VXLANs can only be associated with VXLAN/EVPN or Spectrum X overlays."})


@extras_features("graphql", "statuses", "custom_fields")
class InfiniBandPKey(PrimaryModel):
    """InfiniBand Partition Key."""

    pkey = models.CharField(
        max_length=10,
        help_text="Partition Key value (e.g., '0x8001')",
        validators=[RegexValidator(r"^0x[0-9a-fA-F]{1,4}$", message="PKey must be a hex value like '0x8001'")],
    )
    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, help_text="Descriptive name")
    overlay = models.ForeignKey(
        to=Overlay,
        on_delete=models.SET_NULL,
        related_name="pkeys",
        blank=True,
        null=True,
        help_text="Associated overlay",
    )
    tenant = models.ForeignKey(
        to="tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="infiniband_pkeys",
        blank=True,
        null=True,
        help_text="Owning tenant",
    )
    membership_type = models.CharField(
        max_length=20,
        choices=PKeyMembershipTypeChoices,
        default=PKeyMembershipTypeChoices.FULL,
        help_text="PKey membership type",
    )
    qos_config = models.JSONField(
        blank=True,
        null=True,
        help_text="Optional QoS settings (JSON)",
    )
    status = StatusField()

    class Meta:
        """Meta class."""

        ordering = ["pkey"]
        verbose_name = "InfiniBand PKey"
        verbose_name_plural = "InfiniBand PKeys"
        unique_together = ["pkey", "overlay"]

    _PKEY_RE = re.compile(r"\A0[xX][0-9a-fA-F]{1,4}\Z")

    def __str__(self):
        """Stringify instance."""
        return f"{self.name} ({self.pkey})"

    @staticmethod
    def normalize_pkey(value):
        """Return pkey as '0x' + 4 lowercase hex digits, or the input unchanged if it doesn't match the expected format."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not InfiniBandPKey._PKEY_RE.match(stripped):
            return stripped
        return f"0x{int(stripped, 16):04x}"

    def clean_fields(self, exclude=None):
        """Normalize pkey before field validators run so '0X1' and whitespace are accepted."""
        if isinstance(self.pkey, str):
            self.pkey = self.normalize_pkey(self.pkey)
        super().clean_fields(exclude=exclude)

    def clean(self):
        """Validate PKey associations and normalize the pkey value."""
        super().clean()
        self.pkey = self.normalize_pkey(self.pkey)
        if self.overlay and self.overlay.isolation_type != IsolationTypeChoices.IB_PKEY:
            raise ValidationError({"overlay": "InfiniBand PKeys can only be associated with IB PKey overlays."})

    def save(self, *args, **kwargs):
        """Normalize pkey before persisting."""
        self.pkey = self.normalize_pkey(self.pkey)
        super().save(*args, **kwargs)


@extras_features("graphql", "statuses", "custom_fields")
class InfiniBandMKey(PrimaryModel):
    """InfiniBand Management Key (OpenSM configuration)."""

    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, help_text="Descriptive name")
    mkey_value = models.CharField(
        max_length=18,
        help_text="64-bit Management Key hex value (e.g., '0x0000000000a12c30')",
        validators=[MKEY_HEX_VALIDATOR],
    )
    mkey_per_port = models.BooleanField(
        default=False,
        help_text="Derive a unique MKey per HCA port",
    )
    mkey_lease_period = models.PositiveIntegerField(
        default=60,
        validators=[MaxValueValidator(65535)],
        help_text="MKey lease period in seconds (0=infinite, 1-65535)",
    )
    protect_bits = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(3)],
        help_text="MKey protection bits (0-3)",
    )
    mkey_global_seed = models.CharField(
        max_length=18,
        blank=True,
        help_text="64-bit hex seed for per-port MKey derivation",
        validators=[
            RegexValidator(
                r"^(0x[0-9a-fA-F]{1,16})?$",
                message="Must be a 64-bit hex value (e.g., '0x0000000000a12c30') or empty",
            )
        ],
    )
    ufm_device = models.ForeignKey(
        to="dcim.Device",
        on_delete=models.SET_NULL,
        related_name="infiniband_mkeys",
        blank=True,
        null=True,
        help_text="UFM server device",
    )
    overlay = models.ForeignKey(
        to=Overlay,
        on_delete=models.SET_NULL,
        related_name="mkeys",
        blank=True,
        null=True,
        help_text="Associated IB MKey overlay",
    )
    tenant = models.ForeignKey(
        to="tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="infiniband_mkeys",
        blank=True,
        null=True,
        help_text="Owning tenant",
    )
    status = StatusField()

    class Meta:
        """Meta class."""

        ordering = ["name"]
        verbose_name = "InfiniBand MKey"
        verbose_name_plural = "InfiniBand MKeys"
        unique_together = ["name", "overlay"]

    def __str__(self):
        """Stringify instance."""
        return f"{self.name} ({self.mkey_value})"

    def clean(self):
        """Validate MKey can only be associated with IB MKey overlays."""
        super().clean()
        if self.overlay and self.overlay.isolation_type != IsolationTypeChoices.IB_MKEY:
            raise ValidationError({"overlay": "InfiniBand MKeys can only be associated with IB MKey overlays."})
