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
"""Error hierarchy raised while discovering and registering workflow plugins."""


class WorkflowRegistrationError(Exception):
    """Base class for every failure raised while building the workflow registry.

    Raised directly when a workflow declares metadata that is present but not
    valid; the subclasses below cover the more specific failure modes.
    """


class WorkflowPluginDiscoveryError(WorkflowRegistrationError):
    """An entry point could not be loaded or did not yield a usable descriptor.

    Covers an import or factory failure, a value that is not a
    ``WorkflowPluginDescriptor``, and a descriptor whose ``name`` disagrees with
    the entry-point name it was registered under.
    """


class WorkflowPluginDuplicateError(WorkflowRegistrationError):
    """Two entry points in the plugin group are registered under the same name."""


class WorkflowConflictError(WorkflowRegistrationError):
    """Two plugins contribute the same workflow, activity, API endpoint or CLI name."""


class WorkflowRequiredActivityError(WorkflowRegistrationError):
    """A workflow requires an activity that no installed plugin supplies."""
