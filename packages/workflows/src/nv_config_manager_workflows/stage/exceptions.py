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
"""Failures raised by the stage framework."""

from temporalio.exceptions import ApplicationError


class StageRuntimeFailure(ApplicationError):
    """Exception thrown during stage runtime."""


class StageStateFailure(ApplicationError):
    """Exception thrown for invalid stage states."""

    def __init__(self, message: str) -> None:
        """Init method."""
        super().__init__(message, non_retryable=True)
