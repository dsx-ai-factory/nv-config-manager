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
"""Service-independent settings for network-device connections."""

from typing import TypedDict


class DeviceConnectionSettings(TypedDict):
    """Resolved credentials and dispatch settings supplied by the host service.

    Passwords are ordered authentication candidates (newest rotation first).
    An explicit password is represented by a single candidate; no credentials
    are represented by an empty list. Connections must copy this list before
    changing candidate order or caching a working password.

    Host and port remain constructor arguments. The service uses site to
    resolve credentials before constructing this mapping. Never log settings.
    """

    username: str
    passwords: list[str]
    mock: bool
