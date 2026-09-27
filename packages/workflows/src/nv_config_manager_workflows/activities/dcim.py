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
"""Provider-neutral DCIM lifecycle helpers for reusable activities."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from nv_config_manager_dcim.api import DCIMClient
from nv_config_manager_dcim.errors import DCIMError
from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.runtime import get_dcim_client


@asynccontextmanager
async def dcim_client_session() -> AsyncGenerator[DCIMClient]:
    """Yield a runtime-provided client and translate provider failures for Temporal."""
    client = get_dcim_client()
    try:
        async with client:
            yield client
    except DCIMError as error:
        raise ApplicationError(
            str(error),
            non_retryable=bool(getattr(error, "non_retryable", False)),
        ) from error
