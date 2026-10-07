# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Nautobot job to load baseline NVIDIA Config Manager data for external customers."""

import os
from pathlib import Path

import yaml
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.jobs import Job, register_jobs
from nautobot.core.models.fields import slugify_dashes_to_underscores
from nautobot.dcim.models import (
    DeviceType,
    LocationType,
    Manufacturer,
    Platform,
)
from nautobot.extras.models import (
    ConfigContext,
    ConfigContextSchema,
    CustomField,
    Relationship,
    Role,
    Status,
    Tag,
)
from nautobot.ipam.models import Namespace
from nautobot.tenancy.models import Tenant

name = "Bootstrap"


class LoadBootstrapData(Job):
    """Load bootstrap data for NVIDIA Config Manager external customers from YAML templates."""

    class Meta:
        """Job metadata."""

        name = "Load Bootstrap Data"
        description = (
            "Load manufacturers, roles, tags, custom fields, platforms, device types, tenants, "
            "location types, namespaces, statuses, relationships, config context schemas, and "
            "config contexts from YAML templates"
        )
        has_sensitive_variables = False
        approval_required = False

    def __init__(self):
        """Initialize the job."""
        super().__init__()
        # Path to data templates relative to the job file
        self.data_path = Path(__file__).parent.parent / "data"
        # Get deployment type from environment variable (e.g., 'superpod', 'dgxc', 'azure', 'all')
        self.deployment_type = os.getenv("NV_CONFIG_MANAGER_DEPLOYMENT_TYPE", "all").lower()

    def should_load_item(self, item_data, item_name="item"):
        """Check if an item should be loaded based on deployment_types.

        Args:
            item_data: Dictionary containing item data with optional 'deployment_types' field
            item_name: Name of the item for logging purposes

        Returns:
            bool: True if item should be loaded, False otherwise
        """
        deployment_types = item_data.get("deployment_types", ["all"])

        # If deployment_types is a string, convert to list
        if isinstance(deployment_types, str):
            deployment_types = [deployment_types]

        should_load = self.deployment_type in deployment_types or "all" in deployment_types

        if not should_load:
            self.logger.debug(f"Skipping {item_name} (not in deployment type: {self.deployment_type})")

        return should_load

    def get_content_types(self, content_type_strings):
        """Convert content type strings to ContentType objects.

        Args:
            content_type_strings: List of strings like ['dcim.device', 'dcim.interface']

        Returns:
            list: List of ContentType objects
        """
        content_types = []
        for ct_string in content_type_strings:
            try:
                app_label, model = ct_string.split(".")
                ct = ContentType.objects.get(app_label=app_label, model=model)
                content_types.append(ct)
            except (ValueError, ContentType.DoesNotExist):
                self.logger.warning(f"Could not find content type: {ct_string}")
        return content_types

    def add_content_types(self, obj, content_type_strings):
        """Add content type memberships without removing existing memberships."""
        content_types = self.get_content_types(content_type_strings)
        if content_types:
            obj.content_types.add(*content_types)

    def run(self):
        """Execute the job to load bootstrap data.

        Returns:
            str: Success message
        """
        self.logger.info(
            f"Starting NVIDIA Config Manager Bootstrap Data Load (deployment_type: {self.deployment_type})",
            extra={"grouping": "bootstrap"},
        )

        # Load in dependency order
        self.load_manufacturers()
        self.load_tenants()
        self.load_location_types()
        self.load_namespaces()
        self.load_statuses()
        self.load_roles()
        self.load_tags()
        self.load_custom_fields()
        self.load_platforms()
        self.load_device_types()
        self.load_relationships()
        self.load_config_context_schemas()
        self.load_config_contexts()

        self.logger.info("Bootstrap Data Load Complete!", extra={"grouping": "bootstrap"})
        return "Bootstrap data load completed successfully"

    def load_manufacturers(self):
        """Load manufacturers from YAML template."""
        self.logger.info("Loading Manufacturers", extra={"grouping": "manufacturers"})

        manufacturers_file = self.data_path / "manufacturers.yaml"

        if not manufacturers_file.exists():
            self.logger.failure(f"Manufacturers file not found: {manufacturers_file}")
            return

        try:
            with open(manufacturers_file) as f:
                manufacturers = yaml.safe_load(f)

            if not manufacturers:
                self.logger.warning("No manufacturers found in file")
                return

            for mfg_data in manufacturers:
                try:
                    name = mfg_data.get("name")
                    if not name:
                        self.logger.warning("Skipping manufacturer with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(mfg_data, f"manufacturer '{name}'"):
                        continue

                    mfg, created = Manufacturer.objects.get_or_create(
                        name=name,
                        defaults={"description": mfg_data.get("description", "")},
                    )

                    if created:
                        self.logger.success(
                            f"Created manufacturer: {name}",
                            extra={"grouping": "manufacturers", "object": mfg},
                        )
                    else:
                        self.logger.info(
                            f"Manufacturer already exists: {name}",
                            extra={"grouping": "manufacturers", "object": mfg},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing manufacturer: {mfg_data.get('name', 'unknown')}",
                        extra={"grouping": "manufacturers"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading manufacturers file", extra={"grouping": "manufacturers"})
            self.logger.debug(str(e))

    def _load_single_device_type(self, device_type_file, manufacturer):
        """Load a single device type from a YAML file.

        Args:
            device_type_file: Path to the device type YAML file.
            manufacturer: The Manufacturer instance to associate with.
        """
        with open(device_type_file) as f:
            dt_data = yaml.safe_load(f)

        if not dt_data or "model" not in dt_data:
            self.logger.warning(
                f"Invalid device type file: {device_type_file.name}",
                extra={"grouping": "device_types"},
            )
            return

        if not self.should_load_item(dt_data, f"device type '{dt_data['model']}'"):
            return

        device_type, created = DeviceType.objects.get_or_create(
            manufacturer=manufacturer,
            model=dt_data["model"],
            defaults={
                "part_number": dt_data.get("part_number", ""),
                "u_height": dt_data.get("u_height", 1),
                "is_full_depth": dt_data.get("is_full_depth", True),
            },
        )

        manufacturer_name = manufacturer.name
        if created:
            self.logger.success(
                f"Created device type: {manufacturer_name} {dt_data['model']}",
                extra={"grouping": "device_types", "object": device_type},
            )
        else:
            self.logger.info(
                f"Device type already exists: {manufacturer_name} {dt_data['model']}",
                extra={"grouping": "device_types", "object": device_type},
            )

    def _load_manufacturer_device_types(self, manufacturer_dir):
        """Load all device types for a single manufacturer directory.

        Args:
            manufacturer_dir: Path to the manufacturer subdirectory.
        """
        manufacturer_name = manufacturer_dir.name

        try:
            manufacturer = Manufacturer.objects.get(name=manufacturer_name)
        except Manufacturer.DoesNotExist:
            self.logger.warning(
                f"Manufacturer not found: {manufacturer_name}, skipping device types",
                extra={"grouping": "device_types"},
            )
            return

        for device_type_file in manufacturer_dir.glob("*.yaml"):
            try:
                self._load_single_device_type(device_type_file, manufacturer)
            except Exception as e:
                self.logger.error(
                    f"Error processing device type {device_type_file.name}",
                    extra={"grouping": "device_types"},
                )
                self.logger.debug(str(e))

    def load_device_types(self):
        """Load device types from YAML templates organized by manufacturer."""
        self.logger.info("Loading Device Types", extra={"grouping": "device_types"})

        device_types_path = self.data_path / "device_types"

        if not device_types_path.exists():
            self.logger.failure(f"Device types directory not found: {device_types_path}")
            return

        try:
            for manufacturer_dir in device_types_path.iterdir():
                if manufacturer_dir.is_dir():
                    self._load_manufacturer_device_types(manufacturer_dir)
        except Exception as e:
            self.logger.failure("Error reading device types", extra={"grouping": "device_types"})
            self.logger.debug(str(e))

    def load_roles(self):
        """Load roles from YAML template with optional deployment type filtering."""
        self.logger.info("Loading Roles", extra={"grouping": "roles"})

        roles_file = self.data_path / "roles.yaml"

        if not roles_file.exists():
            self.logger.failure(f"Roles file not found: {roles_file}")
            return

        try:
            with open(roles_file) as f:
                roles = yaml.safe_load(f)

            if not roles:
                self.logger.warning("No roles found in file")
                return

            for role_data in roles:
                try:
                    name = role_data.get("name")
                    if not name:
                        self.logger.warning("Skipping role with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(role_data, f"role '{name}'"):
                        continue

                    role, created = Role.objects.get_or_create(
                        name=name,
                        defaults={
                            "color": role_data.get("color", "grey"),
                            "weight": role_data.get("weight"),
                        },
                    )

                    # Add content_types for both new and existing roles without
                    # removing memberships created by other jobs.
                    if "content_types" in role_data:
                        self.add_content_types(role, role_data["content_types"])
                        role.validated_save()

                    if created:
                        self.logger.success(
                            f"Created role: {name}",
                            extra={"grouping": "roles", "object": role},
                        )
                    else:
                        self.logger.info(
                            f"Role already exists: {name}",
                            extra={"grouping": "roles", "object": role},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing role: {role_data.get('name', 'unknown')}",
                        extra={"grouping": "roles"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading roles file", extra={"grouping": "roles"})
            self.logger.debug(str(e))

    def load_tags(self):
        """Load tags from YAML template."""
        self.logger.info("Loading Tags", extra={"grouping": "tags"})

        tags_file = self.data_path / "tags.yaml"

        if not tags_file.exists():
            self.logger.failure(f"Tags file not found: {tags_file}")
            return

        try:
            with open(tags_file) as f:
                tags = yaml.safe_load(f)

            if not tags:
                self.logger.warning("No tags found in file")
                return

            for tag_data in tags:
                try:
                    name = tag_data.get("name")
                    if not name:
                        self.logger.warning("Skipping tag with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(tag_data, f"tag '{name}'"):
                        continue

                    # Use update_or_create to always set color/description
                    tag, created = Tag.objects.update_or_create(
                        name=name,
                        defaults={
                            "description": tag_data.get("description", ""),
                            "color": tag_data.get("color", "9e9e9e"),
                        },
                    )

                    # Add content_types without removing memberships created by other jobs.
                    if "content_types" in tag_data:
                        self.add_content_types(tag, tag_data["content_types"])

                    if created:
                        self.logger.success(
                            f"Created tag: {name}",
                            extra={"grouping": "tags", "object": tag},
                        )
                    else:
                        self.logger.info(
                            f"Tag already exists: {name}",
                            extra={"grouping": "tags", "object": tag},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing tag: {tag_data.get('name', 'unknown')}",
                        extra={"grouping": "tags"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading tags file", extra={"grouping": "tags"})
            self.logger.debug(str(e))

    def _upsert_custom_field(self, key, cf_data):
        """Create or update a single custom field, logging failures instead of raising."""
        try:
            defaults = {
                "label": cf_data.get("label", key),
                "type": cf_data.get("type", "text"),
                "description": cf_data.get("description", ""),
            }
            # filter_logic is optional; only override Nautobot's default when set.
            if "filter_logic" in cf_data:
                defaults["filter_logic"] = cf_data["filter_logic"]
            cf, created = CustomField.objects.update_or_create(key=key, defaults=defaults)
            # Add content_types without removing memberships created by other jobs.
            if "content_types" in cf_data:
                self.add_content_types(cf, cf_data["content_types"])
        except Exception as exc:
            self.logger.failure(f"Error processing custom field '{key}': {exc}")
            return

        self.logger.success(
            f"{'Created' if created else 'Updated'} custom field: {key}",
            extra={"grouping": "custom_fields", "object": cf},
        )

    def load_custom_fields(self):
        """Load custom fields from YAML template."""
        self.logger.info("Loading Custom Fields", extra={"grouping": "custom_fields"})

        cf_file = self.data_path / "custom_fields.yaml"
        if not cf_file.exists():
            self.logger.failure(f"Custom fields file not found: {cf_file}")
            return

        try:
            with open(cf_file) as f:
                custom_fields = yaml.safe_load(f) or []
        except yaml.YAMLError as exc:
            self.logger.failure(f"Failed to parse custom fields file: {exc}")
            return

        for cf_data in custom_fields:
            if not isinstance(cf_data, dict):
                self.logger.failure(f"Custom field entry is not a dict, skipping: {cf_data!r}")
                continue

            key = cf_data.get("key")
            if not key:
                self.logger.failure(f"Custom field entry missing required 'key': {cf_data}")
                continue
            if not self.should_load_item(cf_data, f"custom field '{key}'"):
                continue

            self._upsert_custom_field(key, cf_data)

    def _resolve_platform_manufacturer(self, name, manufacturer_name):
        """Return the Manufacturer for a platform, or None if unset or not found."""
        if not manufacturer_name:
            return None
        try:
            return Manufacturer.objects.get(name=manufacturer_name)
        except Manufacturer.DoesNotExist:
            self.logger.warning(
                f"Manufacturer not found for platform {name}: {manufacturer_name}",
                extra={"grouping": "platforms"},
            )
            return None

    def load_platforms(self):
        """Load platforms from YAML template."""
        self.logger.info("Loading Platforms", extra={"grouping": "platforms"})

        platforms_file = self.data_path / "platforms.yaml"

        if not platforms_file.exists():
            self.logger.failure(f"Platforms file not found: {platforms_file}")
            return

        try:
            with open(platforms_file) as f:
                platforms = yaml.safe_load(f)

            if not platforms:
                self.logger.warning("No platforms found in file")
                return

            for platform_data in platforms:
                try:
                    name = platform_data.get("name")
                    manufacturer_name = platform_data.get("manufacturer")

                    if not name:
                        self.logger.warning("Skipping platform with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(platform_data, f"platform '{name}'"):
                        continue

                    # Get manufacturer if specified
                    manufacturer = self._resolve_platform_manufacturer(name, manufacturer_name)

                    platform, created = Platform.objects.get_or_create(
                        name=name,
                        defaults={
                            "manufacturer": manufacturer,
                            "description": platform_data.get("description", ""),
                            "napalm_driver": platform_data.get("napalm_driver", ""),
                        },
                    )

                    if created:
                        self.logger.success(
                            f"Created platform: {name}",
                            extra={"grouping": "platforms", "object": platform},
                        )
                    else:
                        self.logger.info(
                            f"Platform already exists: {name}",
                            extra={"grouping": "platforms", "object": platform},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing platform: {platform_data.get('name', 'unknown')}",
                        extra={"grouping": "platforms"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading platforms file", extra={"grouping": "platforms"})
            self.logger.debug(str(e))

    def load_tenants(self):
        """Load tenants from YAML template."""
        self.logger.info("Loading Tenants", extra={"grouping": "tenants"})

        tenants_file = self.data_path / "tenants.yaml"

        if not tenants_file.exists():
            self.logger.warning(f"Tenants file not found: {tenants_file}")
            return

        try:
            with open(tenants_file) as f:
                tenants = yaml.safe_load(f)

            if not tenants:
                self.logger.warning("No tenants found in file")
                return

            for tenant_data in tenants:
                try:
                    name = tenant_data.get("name")
                    if not name:
                        self.logger.warning("Skipping tenant with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(tenant_data, f"tenant '{name}'"):
                        continue

                    tenant, created = Tenant.objects.get_or_create(
                        name=name,
                        defaults={"description": tenant_data.get("description", "")},
                    )

                    if created:
                        self.logger.success(
                            f"Created tenant: {name}",
                            extra={"grouping": "tenants", "object": tenant},
                        )
                    else:
                        self.logger.info(
                            f"Tenant already exists: {name}",
                            extra={"grouping": "tenants", "object": tenant},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing tenant: {tenant_data.get('name', 'unknown')}",
                        extra={"grouping": "tenants"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading tenants file", extra={"grouping": "tenants"})
            self.logger.debug(str(e))

    def _resolve_parent_location_type(self, lt_data):
        """Return the parent LocationType named in ``lt_data``, or None if unset or not found."""
        if not ("parent" in lt_data and lt_data["parent"]):
            return None
        try:
            return LocationType.objects.get(name=lt_data["parent"])
        except LocationType.DoesNotExist:
            self.logger.warning(
                f"Parent location type not found: {lt_data['parent']}",
                extra={"grouping": "location_types"},
            )
            return None

    def load_location_types(self):
        """Load location types from YAML template."""
        self.logger.info("Loading Location Types", extra={"grouping": "location_types"})

        location_types_file = self.data_path / "location_types.yaml"

        if not location_types_file.exists():
            self.logger.warning(f"Location types file not found: {location_types_file}")
            return

        try:
            with open(location_types_file) as f:
                location_types = yaml.safe_load(f)

            if not location_types:
                self.logger.warning("No location types found in file")
                return

            for lt_data in location_types:
                try:
                    name = lt_data.get("name")
                    if not name:
                        self.logger.warning("Skipping location type with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(lt_data, f"location type '{name}'"):
                        continue

                    # Get parent location type if specified
                    parent = self._resolve_parent_location_type(lt_data)

                    lt, created = LocationType.objects.get_or_create(
                        name=name,
                        defaults={
                            "description": lt_data.get("description", ""),
                            "nestable": lt_data.get("nestable", True),
                            "parent": parent,
                        },
                    )

                    # Add content_types without removing memberships created by other jobs.
                    if "content_types" in lt_data:
                        self.add_content_types(lt, lt_data["content_types"])

                    if created:
                        self.logger.success(
                            f"Created location type: {name}",
                            extra={"grouping": "location_types", "object": lt},
                        )
                    else:
                        self.logger.info(
                            f"Location type already exists: {name}",
                            extra={"grouping": "location_types", "object": lt},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing location type: {lt_data.get('name', 'unknown')}",
                        extra={"grouping": "location_types"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure(
                "Error reading location types file",
                extra={"grouping": "location_types"},
            )
            self.logger.debug(str(e))

    def load_namespaces(self):
        """Load namespaces from YAML template."""
        self.logger.info("Loading Namespaces", extra={"grouping": "namespaces"})

        namespaces_file = self.data_path / "namespaces.yaml"

        if not namespaces_file.exists():
            self.logger.warning(f"Namespaces file not found: {namespaces_file}")
            return

        try:
            with open(namespaces_file) as f:
                namespaces = yaml.safe_load(f)

            if not namespaces:
                self.logger.warning("No namespaces found in file")
                return

            for ns_data in namespaces:
                try:
                    name = ns_data.get("name")
                    if not name:
                        self.logger.warning("Skipping namespace with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(ns_data, f"namespace '{name}'"):
                        continue

                    ns, created = Namespace.objects.get_or_create(
                        name=name,
                        defaults={"description": ns_data.get("description", "")},
                    )

                    if created:
                        self.logger.success(
                            f"Created namespace: {name}",
                            extra={"grouping": "namespaces", "object": ns},
                        )
                    else:
                        self.logger.info(
                            f"Namespace already exists: {name}",
                            extra={"grouping": "namespaces", "object": ns},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing namespace: {ns_data.get('name', 'unknown')}",
                        extra={"grouping": "namespaces"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading namespaces file", extra={"grouping": "namespaces"})
            self.logger.debug(str(e))

    def load_statuses(self):
        """Load statuses from YAML template."""
        self.logger.info("Loading Statuses", extra={"grouping": "statuses"})

        statuses_file = self.data_path / "statuses.yaml"

        if not statuses_file.exists():
            self.logger.warning(f"Statuses file not found: {statuses_file}")
            return

        try:
            with open(statuses_file) as f:
                statuses = yaml.safe_load(f)

            if not statuses:
                self.logger.warning("No statuses found in file")
                return

            for status_data in statuses:
                try:
                    name = status_data.get("name")
                    if not name:
                        self.logger.warning("Skipping status with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(status_data, f"status '{name}'"):
                        continue

                    # Use update_or_create to handle color updates
                    status, created = Status.objects.update_or_create(
                        name=name,
                        defaults={
                            "description": status_data.get("description", ""),
                            "color": status_data.get("color", "9e9e9e"),
                        },
                    )

                    # Add content_types without removing memberships created by other jobs.
                    if "content_types" in status_data:
                        self.add_content_types(status, status_data["content_types"])

                    if created:
                        self.logger.success(
                            f"Created status: {name}",
                            extra={"grouping": "statuses", "object": status},
                        )
                    else:
                        self.logger.info(
                            f"Status already exists: {name}",
                            extra={"grouping": "statuses", "object": status},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing status: {status_data.get('name', 'unknown')}",
                        extra={"grouping": "statuses"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure("Error reading statuses file", extra={"grouping": "statuses"})
            self.logger.debug(str(e))

    def _resolve_content_type(self, ct_string):
        """Resolve a 'app_label.model' string to a ContentType."""
        app_label, model = ct_string.split(".")
        return ContentType.objects.get(app_label=app_label, model=model)

    def load_relationships(self):
        """Load relationships from YAML template."""
        self.logger.info("Loading Relationships", extra={"grouping": "relationships"})

        relationships_file = self.data_path / "relationships.yaml"

        if not relationships_file.exists():
            self.logger.warning(f"Relationships file not found: {relationships_file}")
            return

        with open(relationships_file) as f:
            relationships = yaml.safe_load(f)

        if not relationships:
            self.logger.warning("No relationships found in file")
            return

        for rel_data in relationships:
            name = rel_data["name"]

            if not self.should_load_item(rel_data, f"relationship '{name}'"):
                continue

            label = rel_data.get("label", name)
            # Look up by key — the stable unique identifier used by GraphQL.
            # Looking up by label alone fails when another job mutates the
            # label on an existing relationship, causing a duplicate with a
            # "-N" key suffix that breaks GraphQL.
            rel_key = rel_data.get("key") or slugify_dashes_to_underscores(label)

            relationship, created = Relationship.objects.update_or_create(
                key=rel_key,
                defaults={
                    "label": label,
                    "description": rel_data.get("description", ""),
                    "type": rel_data.get("type", "one-to-many"),
                    "source_type": self._resolve_content_type(rel_data["source_type"]),
                    "source_label": rel_data.get("source_label", ""),
                    "destination_type": self._resolve_content_type(rel_data["destination_type"]),
                    "destination_label": rel_data.get("destination_label", ""),
                    "required_on": rel_data.get("required_on", ""),
                },
            )

            if created:
                self.logger.success(
                    f"Created relationship: {name}",
                    extra={"grouping": "relationships", "object": relationship},
                )
            else:
                self.logger.info(
                    f"Relationship already exists: {name}",
                    extra={"grouping": "relationships", "object": relationship},
                )

    def load_config_context_schemas(self):
        """Load config context schemas from YAML template."""
        self.logger.info("Loading Config Context Schemas", extra={"grouping": "config_context_schemas"})

        schemas_file = self.data_path / "config_context_schemas.yaml"

        if not schemas_file.exists():
            self.logger.warning(f"Config context schemas file not found: {schemas_file}")
            return

        try:
            with open(schemas_file) as f:
                schemas = yaml.safe_load(f)

            if not schemas:
                self.logger.warning("No config context schemas found in file")
                return

            for schema_data in schemas:
                try:
                    name = schema_data.get("name")
                    if not name:
                        self.logger.warning("Skipping config context schema with no name")
                        continue

                    # Check deployment type filtering
                    if not self.should_load_item(schema_data, f"config context schema '{name}'"):
                        continue

                    schema, created = ConfigContextSchema.objects.update_or_create(
                        name=name,
                        defaults={
                            "description": schema_data.get("description", ""),
                            "data_schema": schema_data.get("data_schema", {}),
                        },
                    )

                    schema.validated_save()

                    if created:
                        self.logger.success(
                            f"Created config context schema: {name}",
                            extra={"grouping": "config_context_schemas", "object": schema},
                        )
                    else:
                        self.logger.info(
                            f"Config context schema already exists: {name}",
                            extra={"grouping": "config_context_schemas", "object": schema},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing config context schema: {schema_data.get('name', 'unknown')}",
                        extra={"grouping": "config_context_schemas"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure(
                "Error reading config context schemas file",
                extra={"grouping": "config_context_schemas"},
            )
            self.logger.debug(str(e))

    def _apply_config_context_schema(self, name, cc_data, defaults):
        """Add the named config context schema to ``defaults`` if specified and found."""
        if not ("schema" in cc_data and cc_data["schema"]):
            return
        schema_name = cc_data["schema"]
        try:
            schema = ConfigContextSchema.objects.get(name=schema_name)
            defaults["config_context_schema"] = schema
        except ConfigContextSchema.DoesNotExist:
            self.logger.warning(
                f"Schema not found for config context '{name}': {schema_name}",
                extra={"grouping": "config_contexts"},
            )

    def _get_config_context_objects(self, model, object_names, label):
        """Look up ``model`` instances by name, logging a warning for each one not found."""
        objects = []
        for object_name in object_names:
            try:
                objects.append(model.objects.get(name=object_name))
            except model.DoesNotExist:
                self.logger.warning(
                    f"{label} not found for config context: {object_name}",
                    extra={"grouping": "config_contexts"},
                )
        return objects

    def _load_single_config_context(self, cc_data):
        """Create or update a single config context and its role/platform assignments."""
        name = cc_data.get("name")
        if not name:
            self.logger.warning("Skipping config context with no name")
            return

        # Check deployment type filtering
        if not self.should_load_item(cc_data, f"config context '{name}'"):
            return

        # Build defaults dict
        defaults = {
            "description": cc_data.get("description", ""),
            "weight": cc_data.get("weight", 1000),
            "is_active": cc_data.get("is_active", True),
            "data": cc_data.get("data", {}),
        }

        # Get schema if specified
        self._apply_config_context_schema(name, cc_data, defaults)

        cc, created = ConfigContext.objects.update_or_create(
            name=name,
            defaults=defaults,
        )

        # Set roles if specified
        if "roles" in cc_data and cc_data["roles"]:
            roles = self._get_config_context_objects(Role, cc_data["roles"], "Role")
            if roles:
                cc.roles.set(roles)

        # Set platforms if specified
        if "platforms" in cc_data and cc_data["platforms"]:
            platforms = self._get_config_context_objects(Platform, cc_data["platforms"], "Platform")
            if platforms:
                cc.platforms.set(platforms)

        cc.validated_save()

        if created:
            self.logger.success(
                f"Created config context: {name}",
                extra={"grouping": "config_contexts", "object": cc},
            )
        else:
            self.logger.info(
                f"Config context already exists: {name}",
                extra={"grouping": "config_contexts", "object": cc},
            )

    def load_config_contexts(self):
        """Load config contexts from YAML template."""
        self.logger.info("Loading Config Contexts", extra={"grouping": "config_contexts"})

        config_contexts_file = self.data_path / "config_contexts.yaml"

        if not config_contexts_file.exists():
            self.logger.warning(f"Config contexts file not found: {config_contexts_file}")
            return

        try:
            with open(config_contexts_file) as f:
                config_contexts = yaml.safe_load(f)

            if not config_contexts:
                self.logger.warning("No config contexts found in file")
                return

            for cc_data in config_contexts:
                try:
                    self._load_single_config_context(cc_data)
                except Exception as e:
                    self.logger.error(
                        f"Error processing config context: {cc_data.get('name', 'unknown')}",
                        extra={"grouping": "config_contexts"},
                    )
                    self.logger.debug(str(e))

        except Exception as e:
            self.logger.failure(
                "Error reading config contexts file",
                extra={"grouping": "config_contexts"},
            )
            self.logger.debug(str(e))


register_jobs(LoadBootstrapData)
