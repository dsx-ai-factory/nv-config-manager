#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Check the rendered relationship between CNPG Clusters and PodMonitors."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

Document = dict[str, Any]


def nested(document: Document, *keys: str) -> Any:
    """Return a nested value, or None when the path does not exist."""
    value: Any = document
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def check_render(path: Path) -> list[str]:
    """Return ownership and label errors from one Helm render."""
    with path.open(encoding="utf-8") as stream:
        documents = [
            document for document in yaml.safe_load_all(stream) if isinstance(document, dict)
        ]

    clusters = [
        document
        for document in documents
        if document.get("kind") == "Cluster"
        and str(document.get("apiVersion", "")).startswith("postgresql.cnpg.io/")
    ]
    monitors = [
        document
        for document in documents
        if document.get("kind") == "PodMonitor"
        and isinstance(
            nested(document, "spec", "selector", "matchLabels", "cnpg.io/cluster"),
            str,
        )
    ]
    cluster_names = [nested(cluster, "metadata", "name") for cluster in clusters]
    selected_cluster_names = [
        nested(monitor, "spec", "selector", "matchLabels", "cnpg.io/cluster")
        for monitor in monitors
    ]
    raw_monitor_names = [nested(monitor, "metadata", "name") for monitor in monitors]
    operator_monitored_clusters = [
        nested(cluster, "metadata", "name")
        for cluster in clusters
        if nested(cluster, "spec", "monitoring", "enablePodMonitor") is True
    ]

    errors: list[str] = []
    if not clusters:
        errors.append("no CNPG Clusters rendered")
    if operator_monitored_clusters:
        errors.append(
            "operator-managed PodMonitors must remain disabled for chart-monitored "
            f"CNPG Clusters: {', '.join(map(str, operator_monitored_clusters))}"
        )
    cluster_counts = Counter(cluster_names)
    selector_counts = Counter(selected_cluster_names)
    if cluster_counts != selector_counts:
        missing = sorted((cluster_counts - selector_counts).elements(), key=str)
        unexpected = sorted((selector_counts - cluster_counts).elements(), key=str)
        errors.append(
            "CNPG Cluster/PodMonitor selectors differ "
            f"(missing: {missing or 'none'}; unexpected: {unexpected or 'none'}); "
            "check monitoring.podMonitors.cnpg.enabled"
        )
    invalid_name_documents = [
        index
        for index, name in enumerate(raw_monitor_names, start=1)
        if not isinstance(name, str) or not name
    ]
    if invalid_name_documents:
        errors.append(
            "chart-owned CNPG PodMonitor metadata.name is missing or invalid in document(s): "
            + ", ".join(map(str, invalid_name_documents))
        )
    monitor_names = [name for name in raw_monitor_names if isinstance(name, str) and name]
    if len(set(monitor_names)) != len(monitor_names):
        errors.append("chart-owned CNPG PodMonitor names are not unique")
    if collisions := sorted(set(cluster_names).intersection(monitor_names)):
        errors.append(f"PodMonitor names collide with CNPG Clusters: {', '.join(collisions)}")
    overlong_names = sorted(name for name in monitor_names if len(name) > 63)
    if overlong_names:
        errors.append(f"PodMonitor names exceed 63 characters: {', '.join(overlong_names)}")

    print(f"{path}: {len(clusters)} CNPG Clusters, {len(monitors)} CNPG PodMonitors")
    return errors


def main(arguments: list[str]) -> int:
    """Validate each rendered manifest provided on the command line."""
    if len(arguments) < 2:
        print(f"usage: {arguments[0]} RENDERED_MANIFEST [...]", file=sys.stderr)
        return 2

    try:
        failures = [
            f"{path}: {error}"
            for argument in arguments[1:]
            for path in [Path(argument)]
            for error in check_render(path)
        ]
    except (OSError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
