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
"""Prometheus metrics and observability taxonomy for the DHCP service."""

from collections.abc import Iterable

from prometheus_client import Counter, Gauge, Histogram


class SyncOperation:
    """Canonical ``operation`` label values for reconcile/apply failures.

    These identify *where* a reconciliation step broke so operators can tell
    whether an EKS-upgrade recovery failure lived in Redis, config generation,
    applying the config to Kea, hashing, Kea validation, or the PostgreSQL
    lease database -- without having to infer it from container restart counts.
    """

    REDIS_READ = "redis_read"
    CONFIG_GENERATION = "config_generation"
    CONFIG_SET = "config_set"
    HASH_GET = "hash_get"
    CONFIG_TEST = "config_test"
    POSTGRES = "postgres"


# Which operations each process can actually record. The two DHCP confgen
# commands run in separate pods with separate registries, so seeding a label
# value in the wrong one exports a series that can never move off zero.
SYNC_PROCESS_OPERATIONS = (
    SyncOperation.REDIS_READ,
    SyncOperation.CONFIG_SET,
    SyncOperation.HASH_GET,
    SyncOperation.POSTGRES,
)

REFRESH_PROCESS_OPERATIONS = (
    SyncOperation.CONFIG_GENERATION,
    SyncOperation.CONFIG_TEST,
)


class SyncState:
    """Structured ``sync_state`` values emitted on reconcile-loop log lines.

    Emitting an explicit state on every log line lets operators distinguish the
    reconcile-loop phases below when diagnosing a stuck DHCP pod:

    * ``waiting-for-initial-redis-config`` -- no config in Redis yet; the sync
      process is blocked waiting for config-generation to publish one.
    * ``in-sync`` -- running Kea config matches the desired Redis config
      (also emitted right after a successful verified apply/recovery).
    * ``drift-detected`` -- desired config differs from the running config.
    * ``applying`` -- pushing a new config to the local Kea server.
    * ``dependency-error`` -- a dependency (Redis, Kea, PostgreSQL, config
      generation) raised while reconciling.
    """

    WAITING_FOR_INITIAL_REDIS_CONFIG = "waiting-for-initial-redis-config"
    IN_SYNC = "in-sync"
    DRIFT_DETECTED = "drift-detected"
    APPLYING = "applying"
    DEPENDENCY_ERROR = "dependency-error"


DHCP_CONFIG_GENERATION_ERRORS = Counter(
    "nv_config_manager_dhcp_config_generation_errors_total",
    "Total DHCP configuration generation errors",
    ["error_type", "ip_version"],
)

DHCP_QUERY_ERRORS = Counter(
    "nv_config_manager_dhcp_query_errors_total",
    "Total DHCP Nautobot query/data validation errors",
    ["error_type"],
)

DHCP_CONFIG_GENERATION_DURATION = Histogram(
    "nv_config_manager_dhcp_config_generation_duration_seconds",
    "Time taken to generate DHCP configuration",
    ["ip_version"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, float("inf")],
)

DHCP_CACHE_REFRESH_ERRORS = Counter(
    "nv_config_manager_dhcp_cache_refresh_errors_total",
    "Total DHCP cache refresh failures",
    ["ip_version"],
)

# Reconcile/apply failures broken down by the operation that raised. The
# ``operation`` label uses the values in ``SyncOperation`` so operators can see
# WHERE reconciliation broke (redis_read, config_generation, config_set,
# hash_get, config_test, postgres) instead of only a restart count.
DHCP_SYNC_FAILURES = Counter(
    "nv_config_manager_dhcp_sync_failures_total",
    "Total DHCP reconcile/apply failures, labeled by the operation that failed",
    ["operation", "ip_version"],
)

# Number of times the desired (Redis) config diverged from the running (Kea)
# config, i.e. configuration drift / hash mismatch was detected.
DHCP_CONFIG_HASH_MISMATCHES = Counter(
    "nv_config_manager_dhcp_config_hash_mismatches_total",
    "Total detected KEA configuration hash mismatches (drift detected)",
    ["ip_version"],
)

# Unix timestamp (seconds) of the last successful, verified synchronization of
# the desired config onto the local Kea server. Operators alert on the AGE of
# this value: ``time() - <gauge>`` climbing means reconciliation has stalled,
# which is exactly the signal that was missing during the EKS-upgrade incident.
DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP = Gauge(
    "nv_config_manager_dhcp_last_successful_sync_timestamp_seconds",
    "Unix timestamp of the last successful verified DHCP synchronization",
    ["ip_version"],
)


def _seed_failure_counters(ip_version: str, operations: Iterable[str]) -> None:
    """Materialize the failure counter for each ``operation`` at zero."""
    for operation in operations:
        DHCP_SYNC_FAILURES.labels(operation=operation, ip_version=ip_version)


def initialize_sync_metrics(ip_version: int) -> None:
    """Create every config-sync series up front, before the first reconcile.

    prometheus_client only materializes a labeled child on first use, so a pod
    whose initial sync never succeeds would export no
    ``last_successful_sync_timestamp`` series at all -- and an alert on the age
    of a series that does not exist cannot fire. Seeding the gauge at 0 makes
    that pod report an unbounded age instead, which is the intended signal.
    Counters are seeded for the same reason: ``rate()`` needs a prior sample.
    """
    version = str(ip_version)
    DHCP_LAST_SUCCESSFUL_SYNC_TIMESTAMP.labels(ip_version=version).set(0)
    DHCP_CONFIG_HASH_MISMATCHES.labels(ip_version=version)
    DHCP_CACHE_REFRESH_ERRORS.labels(ip_version=version)
    _seed_failure_counters(version, SYNC_PROCESS_OPERATIONS)


def initialize_refresh_metrics(ip_version: int) -> None:
    """Create every config-refresh series up front, before the first refresh.

    Deliberately does not touch ``last_successful_sync_timestamp``: this process
    generates and validates config but never applies it to KEA, so exporting a
    sync timestamp it can never advance would alert forever.
    """
    _seed_failure_counters(str(ip_version), REFRESH_PROCESS_OPERATIONS)
