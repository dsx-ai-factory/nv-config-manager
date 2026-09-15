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

"""Tables for Overlays app."""

import django_tables2 as tables
from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from nautobot.apps.tables import BaseTable, ButtonsColumn, ToggleColumn
from nautobot.core.tables import LinkedCountColumn
from nautobot.core.templatetags import helpers
from nautobot.extras.tables import StatusTableMixin

from nautobot_app_overlays import models

#
# Template code constants for OverlayAssignment tables
#

ASSIGNED_OBJECT_TEMPLATE = """
{% load helpers %}
{{ record.assigned_object|hyperlinked_object }}
"""

MEMBER_DEVICE_TEMPLATE = """
{% load helpers %}
{% if record.assigned_object_type.model == 'device' %}
    {{ record.assigned_object|hyperlinked_object }}
{% elif record.assigned_object_type.model == 'interface' %}
    {{ record.assigned_object.device|hyperlinked_object }}
{% else %}
    {{ None|placeholder }}
{% endif %}
"""

MEMBER_INTERFACE_TEMPLATE = """
{% load helpers %}
{% if record.assigned_object_type.model == 'interface' %}
    {{ record.assigned_object|hyperlinked_object }}
{% else %}
    {{ None|placeholder }}
{% endif %}
"""

MEMBER_LOCATION_TEMPLATE = """
{% load helpers %}
{% if record.assigned_object_type.model == 'device' and record.assigned_object.location %}
    {{ record.assigned_object.location|hyperlinked_object }}
{% elif record.assigned_object_type.model == 'interface' and record.assigned_object.device.location %}
    {{ record.assigned_object.device.location|hyperlinked_object }}
{% else %}
    {{ None|placeholder }}
{% endif %}
"""

MEMBER_RACK_TEMPLATE = """
{% load helpers %}
{% if record.assigned_object_type.model == 'device' and record.assigned_object.rack %}
    {{ record.assigned_object.rack|hyperlinked_object }}
{% elif record.assigned_object_type.model == 'interface' and record.assigned_object.device.rack %}
    {{ record.assigned_object.device.rack|hyperlinked_object }}
{% else %}
    {{ None|placeholder }}
{% endif %}
"""

IMPORT_RT_TEMPLATE = """
{% load helpers %}
{% for rt in record.import_targets.all %}{{ rt|hyperlinked_object }}{% if not forloop.last %}, {% endif %}{% empty %}{{ None|placeholder }}{% endfor %}
"""

EXPORT_RT_TEMPLATE = """
{% load helpers %}
{% for rt in record.export_targets.all %}{{ rt|hyperlinked_object }}{% if not forloop.last %}, {% endif %}{% empty %}{{ None|placeholder }}{% endfor %}
"""

VXLAN_VNI_TEMPLATE = """
{% load helpers %}
{% if record.assigned_object_type.model == 'vxlan' %}
    {{ record.assigned_object.vnid }}
{% else %}
    {{ None|placeholder }}
{% endif %}
"""


_OVERLAY_TYPE_COLUMNS = ["pk", "name", "status", "tenant", "location", "assignment_count", "actions"]


class AssignmentCountColumn(LinkedCountColumn):
    """Assignment count that always links to the filtered assignment list."""

    def render(self, *, record, value):
        """Render the count as a link to the filtered assignment list."""
        if not value:
            return helpers.placeholder(value)
        url = reverse(self.viewname, kwargs=self.view_kwargs)
        if self.url_params:
            url += "?" + urlencode(
                {
                    key: (getattr(record, attr) or settings.FILTERS_NULL_CHOICE_VALUE)
                    for key, attr in self.url_params.items()
                }
            )
        return format_html('<a href="{}" class="badge">{}</a>', url, value)


class OverlayTable(StatusTableMixin, BaseTable):
    """Table for displaying Overlay objects."""

    pk = ToggleColumn()
    name = tables.LinkColumn()
    tenant = tables.LinkColumn()
    location = tables.LinkColumn()
    isolation_type = tables.Column()
    assignment_count = AssignmentCountColumn(
        viewname="plugins:nautobot_app_overlays:overlayassignment_list",
        url_params={"overlay": "pk"},
        verbose_name="Assignments",
    )
    actions = ButtonsColumn(models.Overlay)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.Overlay
        fields = [
            "pk",
            "name",
            "status",
            "tenant",
            "location",
            "isolation_type",
            "assignment_count",
            "actions",
        ]
        default_columns = [
            "pk",
            "name",
            "status",
            "tenant",
            "location",
            "isolation_type",
            "assignment_count",
            "actions",
        ]


class VXLANOverlayTable(OverlayTable):
    """Overlay table for the VXLAN type list."""

    class Meta(OverlayTable.Meta):
        """Meta class."""

        default_columns = _OVERLAY_TYPE_COLUMNS


class NVLinkPartitionOverlayTable(OverlayTable):
    """Overlay table for the NVLink Partition type list."""

    class Meta(OverlayTable.Meta):
        """Meta class."""

        default_columns = _OVERLAY_TYPE_COLUMNS


class IBPKeyOverlayTable(OverlayTable):
    """Overlay table for the IB PKey type list."""

    class Meta(OverlayTable.Meta):
        """Meta class."""

        default_columns = _OVERLAY_TYPE_COLUMNS


class IBMKeyOverlayTable(OverlayTable):
    """Overlay table for the IB MKey type list."""

    class Meta(OverlayTable.Meta):
        """Meta class."""

        default_columns = _OVERLAY_TYPE_COLUMNS


class SpectrumXOverlayTable(OverlayTable):
    """Overlay table for the Spectrum X type list."""

    class Meta(OverlayTable.Meta):
        """Meta class."""

        default_columns = _OVERLAY_TYPE_COLUMNS


class OverlayAssignmentTable(BaseTable):
    """Table for displaying OverlayAssignment objects."""

    pk = ToggleColumn()
    overlay = tables.LinkColumn()
    member_device = tables.TemplateColumn(
        template_code=MEMBER_DEVICE_TEMPLATE,
        verbose_name="Device",
        orderable=False,
    )
    member_interface = tables.TemplateColumn(
        template_code=MEMBER_INTERFACE_TEMPLATE,
        verbose_name="Interface",
        orderable=False,
    )
    assigned_object = tables.TemplateColumn(
        template_code=ASSIGNED_OBJECT_TEMPLATE,
        verbose_name="Member",
        orderable=False,
    )
    assigned_object_type = tables.Column(verbose_name="Type", accessor="assigned_object_type__model")
    device_location = tables.TemplateColumn(
        template_code=MEMBER_LOCATION_TEMPLATE,
        verbose_name="Location",
        orderable=False,
    )
    device_rack = tables.TemplateColumn(
        template_code=MEMBER_RACK_TEMPLATE,
        verbose_name="Rack",
        orderable=False,
    )
    role = tables.Column()
    guid = tables.Column(verbose_name="GUID")
    membership_type = tables.Column(verbose_name="PKey Membership")
    status = tables.Column()
    actions = ButtonsColumn(models.OverlayAssignment)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.OverlayAssignment
        fields = [
            "pk",
            "overlay",
            "member_device",
            "member_interface",
            "assigned_object",
            "assigned_object_type",
            "device_location",
            "device_rack",
            "role",
            "guid",
            "membership_type",
            "status",
            "actions",
        ]
        default_columns = [
            "pk",
            "overlay",
            "member_device",
            "member_interface",
            "assigned_object",
            "assigned_object_type",
            "device_location",
            "device_rack",
            "role",
            "status",
            "actions",
        ]


class OverlayAssignmentInlineTable(BaseTable):
    """Inline assignment table for the overlay detail page."""

    pk = ToggleColumn()
    member_device = tables.TemplateColumn(
        template_code=MEMBER_DEVICE_TEMPLATE,
        verbose_name="Device",
        orderable=False,
    )
    member_interface = tables.TemplateColumn(
        template_code=MEMBER_INTERFACE_TEMPLATE,
        verbose_name="Interface",
        orderable=False,
    )
    assigned_object = tables.TemplateColumn(
        template_code=ASSIGNED_OBJECT_TEMPLATE,
        verbose_name="Member",
        orderable=False,
    )
    assigned_object_type = tables.Column(verbose_name="Type", accessor="assigned_object_type__model")
    device_location = tables.TemplateColumn(
        template_code=MEMBER_LOCATION_TEMPLATE,
        verbose_name="Location",
        orderable=False,
    )
    device_rack = tables.TemplateColumn(
        template_code=MEMBER_RACK_TEMPLATE,
        verbose_name="Rack",
        orderable=False,
    )
    role = tables.Column()
    status = tables.Column()
    actions = ButtonsColumn(models.OverlayAssignment)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.OverlayAssignment
        fields = [
            "pk",
            "member_device",
            "member_interface",
            "assigned_object",
            "assigned_object_type",
            "device_location",
            "device_rack",
            "role",
            "status",
            "actions",
        ]
        default_columns = [
            "pk",
            "member_device",
            "member_interface",
            "assigned_object",
            "assigned_object_type",
            "device_location",
            "device_rack",
            "role",
            "status",
            "actions",
        ]


VXLAN_NAME_TEMPLATE = """
{% load helpers %}
{% if record.assigned_object_type.model == 'vxlan' and record.assigned_object %}
    {{ record.assigned_object|hyperlinked_object }}
{% else %}
    {{ None|placeholder }}
{% endif %}
"""


class VXLANOverlayAssignmentInlineTable(BaseTable):
    """Overlay assignments table shown on the VXLAN detail page."""

    pk = ToggleColumn()
    overlay = tables.Column(linkify=True)
    import_targets = tables.TemplateColumn(
        template_code=IMPORT_RT_TEMPLATE,
        verbose_name="Import RTs",
        orderable=False,
    )
    export_targets = tables.TemplateColumn(
        template_code=EXPORT_RT_TEMPLATE,
        verbose_name="Export RTs",
        orderable=False,
    )
    status = tables.Column()
    actions = ButtonsColumn(models.OverlayAssignment)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.OverlayAssignment
        fields = [
            "pk",
            "overlay",
            "import_targets",
            "export_targets",
            "status",
            "actions",
        ]
        default_columns = [
            "pk",
            "overlay",
            "import_targets",
            "export_targets",
            "status",
            "actions",
        ]


class VXLANAssignmentInlineTable(BaseTable):
    """VXLAN assignments table shown on the overlay detail page."""

    pk = ToggleColumn()
    vxlan = tables.TemplateColumn(
        template_code=VXLAN_NAME_TEMPLATE,
        verbose_name="VXLAN",
        orderable=False,
    )
    vni = tables.TemplateColumn(
        template_code=VXLAN_VNI_TEMPLATE,
        verbose_name="VNI",
        orderable=False,
    )
    import_targets = tables.TemplateColumn(
        template_code=IMPORT_RT_TEMPLATE,
        verbose_name="Import RTs",
        orderable=False,
    )
    export_targets = tables.TemplateColumn(
        template_code=EXPORT_RT_TEMPLATE,
        verbose_name="Export RTs",
        orderable=False,
    )
    status = tables.Column()
    actions = ButtonsColumn(models.OverlayAssignment)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.OverlayAssignment
        fields = [
            "pk",
            "vxlan",
            "vni",
            "import_targets",
            "export_targets",
            "status",
            "actions",
        ]
        default_columns = [
            "pk",
            "vxlan",
            "vni",
            "import_targets",
            "export_targets",
            "status",
            "actions",
        ]


class VXLANTable(StatusTableMixin, BaseTable):
    """Table for displaying VXLAN objects."""

    pk = ToggleColumn()
    name = tables.LinkColumn()
    vnid = tables.Column()
    vni_type = tables.Column(verbose_name="VNI Type")
    l3_vlan_id = tables.Column(verbose_name="L3 VLAN")
    namespace = tables.LinkColumn()
    overlay = tables.LinkColumn()
    vlan = tables.LinkColumn()
    vrf = tables.LinkColumn()
    tenant = tables.LinkColumn()
    actions = ButtonsColumn(models.VXLAN)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.VXLAN
        fields = [
            "pk",
            "name",
            "status",
            "vnid",
            "vni_type",
            "l3_vlan_id",
            "namespace",
            "overlay",
            "vlan",
            "vrf",
            "tenant",
            "actions",
        ]
        default_columns = [
            "pk",
            "name",
            "status",
            "vnid",
            "vni_type",
            "namespace",
            "overlay",
            "actions",
        ]


class InfiniBandPKeyTable(StatusTableMixin, BaseTable):
    """Table for displaying InfiniBandPKey objects."""

    pk = ToggleColumn()
    name = tables.LinkColumn()
    pkey = tables.Column()
    overlay = tables.LinkColumn()
    tenant = tables.LinkColumn()
    membership_type = tables.Column()
    actions = ButtonsColumn(models.InfiniBandPKey)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.InfiniBandPKey
        fields = [
            "pk",
            "name",
            "status",
            "pkey",
            "overlay",
            "tenant",
            "membership_type",
            "actions",
        ]
        default_columns = [
            "pk",
            "name",
            "status",
            "pkey",
            "overlay",
            "membership_type",
            "actions",
        ]


class InfiniBandMKeyTable(StatusTableMixin, BaseTable):
    """Table for displaying InfiniBandMKey objects."""

    pk = ToggleColumn()
    name = tables.LinkColumn()
    mkey_value = tables.Column(verbose_name="MKey Value")
    mkey_per_port = tables.BooleanColumn(verbose_name="Per Port")
    mkey_lease_period = tables.Column(verbose_name="Lease (s)")
    protect_bits = tables.Column(verbose_name="Protect Bits")
    ufm_device = tables.LinkColumn(verbose_name="UFM Device")
    overlay = tables.LinkColumn()
    tenant = tables.LinkColumn()
    actions = ButtonsColumn(models.InfiniBandMKey)

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.InfiniBandMKey
        fields = [
            "pk",
            "name",
            "status",
            "mkey_value",
            "mkey_per_port",
            "mkey_lease_period",
            "protect_bits",
            "ufm_device",
            "overlay",
            "tenant",
            "actions",
        ]
        default_columns = [
            "pk",
            "name",
            "status",
            "mkey_value",
            "mkey_per_port",
            "overlay",
            "ufm_device",
            "actions",
        ]


class OverlayMembershipInlineTable(BaseTable):
    """Table for displaying OverlayAssignment objects in Device/Interface detail views."""

    overlay = tables.LinkColumn()
    isolation_type = tables.Column(accessor="overlay__isolation_type", verbose_name="Isolation Type")
    role = tables.Column()
    status = tables.Column()

    class Meta(BaseTable.Meta):
        """Meta class."""

        model = models.OverlayAssignment
        fields = [
            "overlay",
            "isolation_type",
            "role",
            "status",
        ]
        default_columns = [
            "overlay",
            "isolation_type",
            "role",
            "status",
        ]
