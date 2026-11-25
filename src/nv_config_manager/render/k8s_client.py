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
"""Shared Kubernetes API client config loading for rollout.py and
activation_state.py -- both need the same in-cluster-or-kubeconfig setup."""

from __future__ import annotations

from kubernetes import config as k8s_config
from kubernetes.config.config_exception import ConfigException

_loaded = False


def load_k8s_config() -> None:
    """Load in-cluster config (normal production/local-dev pod case), falling
    back to the local kubeconfig for running this code outside a pod (e.g. a
    developer's own tests). Cached after the first successful load."""
    global _loaded
    if _loaded:
        return
    try:
        k8s_config.load_incluster_config()
    except ConfigException:
        k8s_config.load_kube_config()
    _loaded = True
