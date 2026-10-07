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
"""Errors raised for an invalid workflow form declaration."""


class WorkflowFormContractError(ValueError):
    """A workflow input model declares a form that breaks the v1 form contract.

    Registration treats it apart from ordinary registration errors: an invalid
    built-in form stops startup, while an invalid third-party form only makes
    that workflow's form unavailable.
    """


__all__ = ["WorkflowFormContractError"]
