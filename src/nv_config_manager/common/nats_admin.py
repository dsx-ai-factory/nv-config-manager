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
"""Compatibility exports for shared NATS guidance."""

from nv_config_manager_infrastructure.nats_admin import (
    CONSUMER_ACK_WAIT_SECONDS as CONSUMER_ACK_WAIT_SECONDS,
)
from nv_config_manager_infrastructure.nats_admin import CONSUMER_MAX_DELIVER as CONSUMER_MAX_DELIVER
from nv_config_manager_infrastructure.nats_admin import (
    consumer_api_subjects as consumer_api_subjects,
)
from nv_config_manager_infrastructure.nats_admin import (
    expected_consumer_configuration as expected_consumer_configuration,
)
from nv_config_manager_infrastructure.nats_admin import (
    is_nats_permissions_error as is_nats_permissions_error,
)
from nv_config_manager_infrastructure.nats_admin import (
    provision_consumer_request as provision_consumer_request,
)
from nv_config_manager_infrastructure.nats_admin import (
    reset_consumer_request as reset_consumer_request,
)
from nv_config_manager_infrastructure.nats_admin import (
    update_consumer_request as update_consumer_request,
)
