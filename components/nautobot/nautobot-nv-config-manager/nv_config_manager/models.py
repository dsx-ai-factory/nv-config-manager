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
"""Model definitions for the Config Manager plugin."""

from typing import Any

from django.core.validators import URLValidator
from django.db import models
from django.db.models import OneToOneField
from django.urls import reverse
from nautobot.core.models.generics import PrimaryModel
from nautobot.extras.utils import extras_features


class ConfigManagerConfigStatus(PrimaryModel):  # pylint: disable=too-many-ancestors
    """Abstract base for the Config Manager config-status models."""

    config_store_instance = models.URLField(max_length=255)
    path = models.CharField(max_length=255)
    updated = models.DateTimeField()
    updated_by = models.CharField(max_length=255)
    commit_id = models.CharField(max_length=255)
    commit_message = models.CharField(max_length=255)

    def save(self, *args, **kwargs):
        """Run a full clean to validate the data."""
        self.full_clean()
        super().save(*args, **kwargs)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Validation Messages."""
        super().__init__(*args, **kwargs)
        for validator in self._meta.get_field("config_store_instance").validators:
            if not isinstance(validator, URLValidator):
                continue
            validator.schemes = ["http", "https"]
            validator.message = "Must be a valid http:// or https:// URL."

    class Meta:
        """Metaclass Attributes."""

        abstract = True


@extras_features("graphql")
class ConfigManagerDeviceStatus(PrimaryModel):  # pylint: disable=too-many-ancestors
    """Per-device config-management state."""

    device: OneToOneField = models.OneToOneField(
        to="dcim.Device",
        on_delete=models.CASCADE,
    )
    render_enabled = models.BooleanField(default=False)
    ztp_enabled = models.BooleanField(default=False)
    deploy_enabled = models.BooleanField(default=False)
    backup_enabled = models.BooleanField(default=False)
    is_aggregate_managed = models.BooleanField(default=False)

    @property
    def is_pending(self) -> bool:
        """Calculate if config is pending."""
        try:
            if (
                self.intended_config  # pylint: disable=maybe-no-member
                and self.backup_config  # pylint: disable=maybe-no-member
                and self.intended_config.commit_id  # pylint: disable=maybe-no-member
                != self.backup_config.deployed_commit_id  # pylint: disable=maybe-no-member
            ):
                return True
        except (IntendedConfig.DoesNotExist, BackupConfig.DoesNotExist):
            return False
        return False

    def save(self, *args, **kwargs):
        """Update config manager device id to match Device id."""
        if (
            self.pk != self.device.pk  # pylint: disable=access-member-before-definition no-member
        ):
            self.pk = (  # pylint: disable=attribute-defined-outside-init
                self.device.pk  # pylint: disable=no-member
            )
        super().save(*args, **kwargs)

    def __str__(self):
        """Return a simple string if model is called."""
        return f"{self.device}"

    def get_absolute_url(self, *args, **kwargs):
        """Return canonical URL for instances of the model."""
        return reverse("plugins:nv_config_manager:configmanagerdevicestatus", args=[self.pk])

    class Meta:
        """Meta Class Attributes."""

        # Consumers page this model over GraphQL with limit/offset. Without a
        # total order the database may walk the table differently per page, so
        # a row can land in two pages or in none.
        ordering = ["pk"]
        verbose_name = "Config Manager Device"
        verbose_name_plural = "Config Manager Devices"


@extras_features("graphql")
class IntendedConfig(ConfigManagerConfigStatus):  # pylint: disable=too-many-ancestors
    """Intended config snapshot for a ConfigManagerDeviceStatus."""

    device_id = models.OneToOneField(
        to=ConfigManagerDeviceStatus,
        on_delete=models.CASCADE,
        related_name="intended_config",
    )
    template_version = models.CharField(max_length=255)

    class Meta(ConfigManagerConfigStatus.Meta):
        """Metaclass Attributes."""

        verbose_name = "Intended Config Settings"

    def save(self, *args, **kwargs):
        """Pin intended_config pk to the device pk."""
        if self.pk != self.device_id.pk:  # pylint: disable=access-member-before-definition no-member
            self.pk = self.device_id.pk  # pylint: disable=attribute-defined-outside-init no-member
        super().save(*args, **kwargs)

    def __str__(self):
        """Return a simple string if model is called."""
        return f"Intended Config Settings - {self.device_id}"

    def get_absolute_url(self, *args, **kwargs):
        """Return canonical URL for instances of the model."""
        return reverse("plugins:nv_config_manager:intendedconfig", args=[self.device_id])


@extras_features("graphql")
class BackupConfig(ConfigManagerConfigStatus):  # pylint: disable=too-many-ancestors
    """Backup config snapshot for a ConfigManagerDeviceStatus."""

    device_id = models.OneToOneField(
        to=ConfigManagerDeviceStatus,
        on_delete=models.CASCADE,
        related_name="backup_config",
    )
    deployed_commit_id = models.CharField(max_length=255, blank=True)
    workflow_id = models.CharField(max_length=255)

    class Meta(ConfigManagerConfigStatus.Meta):
        """Metaclass Attributes."""

        verbose_name = "Backup Config Settings"

    def save(self, *args, **kwargs):
        """Pin backup_config pk to the device pk."""
        if (
            self.pk != self.device_id.pk  # pylint: disable=access-member-before-definition no-member
        ):
            self.pk = (  # pylint: disable=attribute-defined-outside-init no-member
                self.device_id.pk
            )
        super().save(*args, **kwargs)

    def __str__(self):
        """Return a simple string if model is called."""
        return f"Backup Config Settings - {self.device_id}"

    def get_absolute_url(self, *args, **kwargs):
        """Return canonical URL for instances of the model."""
        return reverse("plugins:nv_config_manager:backupconfig", args=[self.device_id])
