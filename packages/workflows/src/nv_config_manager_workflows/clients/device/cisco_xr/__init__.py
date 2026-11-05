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
"""Cisco IOS XR support: NETCONF device client, configuration handling and operational data.

- :mod:`.connection`: :class:`CiscoIosXrConnection`, the workflow semantics (diff, approval,
  commit, verify, confirm, diagnostics).
- :mod:`.netconf`: the NETCONF session (lock, copy-config, diff action, commit, rollback).
- :mod:`.config`: text handling (normalize, parse, the router's diff, lockout guard).
- :mod:`.operational`: YANG filters and reply parsers for diagnostics and link state.

Import :class:`~.connection.CiscoIosXrConnection` from :mod:`.connection`: client package
initializers import nothing, so importing the package does not load ncclient.
"""
