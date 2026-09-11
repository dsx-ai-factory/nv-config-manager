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
"""Reusable Temporal workflow and activity library for NVIDIA Config Manager network automation."""

from nv_config_manager_workflows.lock import (
    LockBackendNotConfiguredError,
    acquire_lock,
    release_lock,
    renew_lock,
)
from nv_config_manager_workflows.runtime import (
    NatsConfigurationProvider,
    NatsNotConfiguredError,
    RuntimeConfigurationError,
    SlackConfigurationProvider,
    SlackNotConfiguredError,
    UIBaseURLNotConfiguredError,
    UIBaseURLProvider,
    configure_nats,
    configure_runtime,
    configure_slack,
    configure_ui_base_url,
    get_nats_configuration,
    get_slack_configuration,
    get_ui_base_url,
)
from nv_config_manager_workflows.search_attributes import upsert_missing_search_attributes
from nv_config_manager_workflows.secrets import (
    CredentialConfig,
    CredentialSection,
    get_credential,
    get_rotation_passwords,
    get_site_slug,
    select_credential_source,
)
from nv_config_manager_workflows.workflow_references import (
    DeviceReferences,
    LocationReference,
    OptionalDeviceReference,
    OptionalLocationReference,
    WorkflowReference,
    WorkflowReferenceKind,
)

__all__ = [
    # lock
    "LockBackendNotConfiguredError",
    "acquire_lock",
    "release_lock",
    "renew_lock",
    # runtime
    "NatsConfigurationProvider",
    "NatsNotConfiguredError",
    "RuntimeConfigurationError",
    "SlackConfigurationProvider",
    "SlackNotConfiguredError",
    "UIBaseURLNotConfiguredError",
    "UIBaseURLProvider",
    "configure_nats",
    "configure_runtime",
    "configure_slack",
    "configure_ui_base_url",
    "get_nats_configuration",
    "get_slack_configuration",
    "get_ui_base_url",
    # search_attributes
    "upsert_missing_search_attributes",
    # secrets
    "CredentialConfig",
    "CredentialSection",
    "get_credential",
    "get_rotation_passwords",
    "get_site_slug",
    "select_credential_source",
    # workflow_references
    "DeviceReferences",
    "LocationReference",
    "OptionalDeviceReference",
    "OptionalLocationReference",
    "WorkflowReference",
    "WorkflowReferenceKind",
]
