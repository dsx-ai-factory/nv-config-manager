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
"""Simple HealthCheck API for DHCP Server."""

import argparse
import asyncio
import base64
import binascii
from collections.abc import AsyncIterator, Coroutine
from configparser import ConfigParser
from contextlib import asynccontextmanager
from ipaddress import ip_address
from typing import Any

import uvicorn
from aiohttp import ClientError, ClientResponseError
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as instrumentator_metrics
from pydantic import IPvAnyAddress, IPvAnyNetwork

from nv_config_manager.common.auth import install_identity_probe
from nv_config_manager.common.config import load_config
from nv_config_manager.common.log import configure_logging
from nv_config_manager.common.telemetry import (
    group_fastapi_status_codes,
    instrument_fastapi_app,
    setup_tracing,
)
from nv_config_manager.dhcp.kea import IpVersion, KeaClient, KeaException
from nv_config_manager.dhcp.lease_dashboard import (
    DhcpSummaryResponse,
    LeasePageResponse,
    LeaseRecord,
    PoolPageResponse,
    ReservationPageResponse,
    ReservationRecord,
    build_dhcp_summary,
    build_lease,
    build_lease_list,
    build_pool_list,
    build_reservation,
    build_reservation_list,
    filter_lease_records,
    filter_pool_records,
    filter_reservation_records,
    lease_deleted,
    lease_page_details,
)
from nv_config_manager.dhcp.redis import RedisClient

configure_logging(service="dhcp")
setup_tracing("dhcp")

_MAX_KEA_LEASE_PAGES_PER_REQUEST = 10


def _install_cors(application: FastAPI) -> None:
    """Allow configured UI origins to call the DHCP API with credentials."""
    config = load_config()
    if not config.has_section("dhcp"):
        return

    origins = [
        origin.strip()
        for origin in config.get("dhcp", "cors_origins", fallback="").split(",")
        if origin.strip()
    ]
    if "*" in origins:
        raise ValueError(
            "dhcp.cors_origins must contain explicit origins when credentials are enabled"
        )
    if origins:
        application.add_middleware(
            CORSMiddleware,  # type: ignore[arg-type]
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )


app = FastAPI()
_install_cors(app)
instrument_fastapi_app(app)

CACHE_LAST_REFRESH = Gauge(
    "cache_last_refresh_timestamp_seconds",
    "Unix timestamp of last successful DHCP cache refresh",
    ["ip_version"],
    namespace="nv-config-manager",
    subsystem="dhcp",
)

instrumentator = Instrumentator(
    should_group_status_codes=group_fastapi_status_codes(),
    excluded_handlers=["/healthcheck", "/metrics"],
)
instrumentator.add(
    instrumentator_metrics.default(
        metric_namespace="nv-config-manager",
        metric_subsystem="dhcp",
    )
)
instrumentator.instrument(app)


async def _gather_requests(
    *requests: Coroutine[Any, Any, Any],
) -> tuple[Any, ...]:
    """Run requests concurrently and drain every task before returning or raising."""
    tasks = tuple(asyncio.create_task(request) for request in requests)
    try:
        return tuple(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _fetch_summary_sources(
    client: KeaClient,
    ip_version: IpVersion,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch summary sources and drain every task before returning or raising."""
    config, statistics = await _gather_requests(
        client.get_config(ip_version),
        client.get_statistics(ip_version),
    )
    return config, statistics


@asynccontextmanager
async def _kea_lease_client() -> AsyncIterator[KeaClient]:
    """Map KEA client errors for lease routes and always close the client."""
    client = KeaClient.from_config()
    try:
        yield client
    except ClientResponseError as exc:
        raise HTTPException(status_code=500, detail=str(exc.message)) from exc
    except (TimeoutError, ClientError, KeaException) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await client.close()


def _resolve_address_version(
    ip_address: IPvAnyAddress,
    ip_version: IpVersion | None,
) -> IpVersion:
    """Infer the DHCP service version unless the caller supplies a matching value."""
    inferred_version = IpVersion(ip_address.version)
    if ip_version is not None and ip_version != inferred_version:
        raise HTTPException(
            status_code=422,
            detail=f"IP address version does not match ip_version={ip_version}",
        )
    return ip_version or inferred_version


def _normalize_subnet_filter(
    subnet: IPvAnyNetwork | None,  # ty: ignore[unsupported-operator]
    ip_version: IpVersion,
) -> str | None:
    """Validate and normalize an optional collection subnet filter."""
    if subnet is not None and subnet.version != ip_version:
        raise HTTPException(
            status_code=422,
            detail=f"IP subnet version does not match ip_version={ip_version}",
        )
    return str(subnet) if subnet is not None else None


def _encode_lease_cursor(from_address: str) -> str:
    """Encode the DHCP server's paging address as an opaque API cursor."""
    return base64.urlsafe_b64encode(from_address.encode()).decode().rstrip("=")


def _decode_lease_cursor(cursor: str | None, ip_version: IpVersion) -> str:
    """Decode and validate an opaque lease cursor for the selected address family."""
    if cursor is None:
        return "start"
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        ).decode()
        address = ip_address(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid lease cursor") from exc
    if address.version != ip_version:
        raise HTTPException(
            status_code=422,
            detail=f"Lease cursor does not match ip_version={ip_version}",
        )
    return str(address)


def _encode_offset_cursor(resource: str, offset: int, ip_version: IpVersion) -> str:
    """Encode a config-derived collection offset as an opaque cursor."""
    value = f"{resource}:{ip_version}:{offset}"
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_offset_cursor(
    cursor: str | None,
    resource: str,
    ip_version: IpVersion,
) -> int:
    """Decode and validate a config-derived collection cursor."""
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        ).decode()
        cursor_resource, cursor_version, raw_offset = decoded.split(":", maxsplit=2)
        version = int(cursor_version)
        offset = int(raw_offset)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {resource} cursor") from exc
    if cursor_resource != resource or offset < 0:
        raise HTTPException(status_code=422, detail=f"Invalid {resource} cursor")
    if version != ip_version:
        raise HTTPException(
            status_code=422,
            detail=f"{resource.title()} cursor does not match ip_version={ip_version}",
        )
    return offset


def _slice_offset_page[PageItem](
    items: list[PageItem],
    offset: int,
    limit: int,
) -> tuple[list[PageItem], int, int | None]:
    """Return one bounded page, its exact total, and the next offset."""
    total_count = len(items)
    page = items[offset : offset + limit]
    next_offset = offset + len(page)
    return page, total_count, next_offset if next_offset < total_count else None


async def _collect_lease_page(
    client: KeaClient,
    config_payload: list[dict[str, Any]],
    initial_lease_payload: list[dict[str, Any]],
    *,
    from_address: str,
    ip_version: IpVersion,
    limit: int,
    search: str | None,
    subnet: str | None,
) -> LeasePageResponse:
    """Collect a filtered page while advancing through bounded KEA pages."""
    leases: list[LeaseRecord] = []
    lease_payload = initial_lease_payload
    seen_addresses: set[str] = set()
    scanned_pages = 1

    while True:
        page_leases = build_lease_list(config_payload, lease_payload, ip_version=ip_version)
        if subnet is not None:
            page_leases = [lease for lease in page_leases if lease.subnet == subnet]
        page_leases = filter_lease_records(page_leases, search)
        raw_count, last_address = lease_page_details(lease_payload, ip_version=ip_version)

        if leases and len(leases) + len(page_leases) > limit:
            return LeasePageResponse(
                leases=leases,
                next_cursor=_encode_lease_cursor(from_address),
            )
        leases.extend(page_leases)

        if raw_count < limit or last_address is None:
            return LeasePageResponse(leases=leases)
        if last_address == from_address or last_address in seen_addresses:
            raise KeaException("KEA lease pagination did not advance")

        next_cursor = _encode_lease_cursor(last_address)
        if len(leases) >= limit:
            return LeasePageResponse(leases=leases, next_cursor=next_cursor)
        if scanned_pages >= _MAX_KEA_LEASE_PAGES_PER_REQUEST:
            return LeasePageResponse(leases=leases, next_cursor=next_cursor)

        seen_addresses.add(last_address)
        from_address = last_address
        lease_payload = await client.get_lease_page(
            limit,
            version=ip_version,
            from_address=from_address,
        )
        scanned_pages += 1


def main() -> None:
    """CLI entrypoint for DHCP API."""

    parser = argparse.ArgumentParser(description="DHCP API Server")
    parser.add_argument("--port", type=int, default=9000, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    uvicorn.run(
        "nv_config_manager.dhcp.api:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
        log_config=None,
        loop="asyncio",
    )


def sanitize_config(config: Any) -> None:
    """Recursively remove user and password keys from the config dictionary."""
    if isinstance(config, dict):
        config.pop("user", None)
        config.pop("password", None)
        for value in config.values():
            sanitize_config(value)
    elif isinstance(config, list):
        for item in config:
            sanitize_config(item)


@app.get("/config")
async def get_config(request: Request, ip_version: int = 4) -> Any:
    """Get the running KEA DHCP Configuration."""
    client = KeaClient.from_config()
    try:
        raw_config = await client.get_config(ip_version)
        sanitize_config(raw_config)
        return raw_config
    except TimeoutError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ClientResponseError as exc:
        raise HTTPException(status_code=500, detail=str(exc.message)) from exc
    finally:
        await client.close()


@app.get(
    "/lease/{ip_address}",
    response_model=LeaseRecord,
    responses={404: {"description": "Lease not found"}},
)
async def get_lease(
    request: Request,
    ip_address: IPvAnyAddress,
    ip_version: IpVersion | None = None,
) -> LeaseRecord:
    """Return one normalized lease from the selected DHCP service."""
    ip_version = _resolve_address_version(ip_address, ip_version)
    async with _kea_lease_client() as client:
        lease_payload, config_payload = await _gather_requests(
            client.get_lease(str(ip_address), version=ip_version),
            client.get_config(ip_version),
        )
        lease = build_lease(
            config_payload,
            lease_payload,
            ip_version=ip_version,
        )
        if lease is None:
            raise HTTPException(status_code=404, detail=f"Lease {ip_address} was not found")
        return lease


@app.get("/lease", response_model=LeasePageResponse)
async def list_leases(
    request: Request,
    ip_version: IpVersion = IpVersion.V4,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=128),
    search: str | None = Query(default=None, max_length=256),
    subnet: IPvAnyNetwork | None = None,  # ty: ignore[unsupported-operator]
) -> LeasePageResponse:
    """Return a cursor-paginated, optionally filtered page of normalized leases."""
    subnet_filter = _normalize_subnet_filter(subnet, ip_version)
    from_address = _decode_lease_cursor(cursor, ip_version)
    async with _kea_lease_client() as client:
        lease_payload, config_payload = await _gather_requests(
            client.get_lease_page(
                limit,
                version=ip_version,
                from_address=from_address,
            ),
            client.get_config(ip_version),
        )
        return await _collect_lease_page(
            client,
            config_payload,
            lease_payload,
            from_address=from_address,
            ip_version=ip_version,
            limit=limit,
            search=search,
            subnet=subnet_filter,
        )


@app.get(
    "/reservation/{ip_address}",
    response_model=ReservationRecord,
    responses={404: {"description": "Reservation not found"}},
)
async def get_reservation(
    request: Request,
    ip_address: IPvAnyAddress,
    ip_version: IpVersion | None = None,
) -> ReservationRecord:
    """Return one normalized reservation from the selected DHCP configuration."""
    ip_version = _resolve_address_version(ip_address, ip_version)
    async with _kea_lease_client() as client:
        config_payload = await client.get_config(ip_version)
    reservation = build_reservation(
        config_payload,
        ip_address,
        ip_version=ip_version,
    )
    if reservation is None:
        raise HTTPException(status_code=404, detail=f"Reservation {ip_address} was not found")
    return reservation


@app.get("/reservation", response_model=ReservationPageResponse)
async def list_reservations(
    request: Request,
    ip_version: IpVersion = IpVersion.V4,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=128),
    search: str | None = Query(default=None, max_length=256),
    subnet: IPvAnyNetwork | None = None,  # ty: ignore[unsupported-operator]
) -> ReservationPageResponse:
    """Return a cursor-paginated, optionally filtered reservation page."""
    subnet_filter = _normalize_subnet_filter(subnet, ip_version)
    offset = _decode_offset_cursor(cursor, "reservation", ip_version)
    async with _kea_lease_client() as client:
        config_payload = await client.get_config(ip_version)
    reservations = build_reservation_list(config_payload, ip_version=ip_version)
    if subnet_filter is not None:
        reservations = [
            reservation for reservation in reservations if reservation.subnet == subnet_filter
        ]
    filtered_reservations = filter_reservation_records(reservations, search)
    page, total_count, next_offset = _slice_offset_page(
        filtered_reservations,
        offset,
        limit,
    )
    return ReservationPageResponse(
        reservations=page,
        total_count=total_count,
        next_cursor=(
            _encode_offset_cursor("reservation", next_offset, ip_version)
            if next_offset is not None
            else None
        ),
    )


@app.get("/pool", response_model=PoolPageResponse)
async def list_pools(
    request: Request,
    ip_version: IpVersion = IpVersion.V4,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=128),
    search: str | None = Query(default=None, max_length=256),
    subnet: IPvAnyNetwork | None = None,  # ty: ignore[unsupported-operator]
) -> PoolPageResponse:
    """Return a cursor-paginated, optionally filtered configured-pool page."""
    subnet_filter = _normalize_subnet_filter(subnet, ip_version)
    offset = _decode_offset_cursor(cursor, "pool", ip_version)
    async with _kea_lease_client() as client:
        config_payload = await client.get_config(ip_version)
    pools = build_pool_list(
        config_payload,
        ip_version=ip_version,
    )
    if subnet_filter is not None:
        pools = [pool for pool in pools if pool.subnet == subnet_filter]
    filtered_pools = filter_pool_records(pools, search)
    page, total_count, next_offset = _slice_offset_page(filtered_pools, offset, limit)
    return PoolPageResponse(
        pools=page,
        total_count=total_count,
        next_cursor=(
            _encode_offset_cursor("pool", next_offset, ip_version)
            if next_offset is not None
            else None
        ),
    )


@app.delete(
    "/lease/{ip_address}",
    status_code=204,
    responses={404: {"description": "Lease not found"}},
)
async def delete_lease(
    request: Request,
    ip_address: IPvAnyAddress,
    ip_version: IpVersion | None = None,
) -> Response:
    """Delete one lease from the selected DHCP service."""
    ip_version = _resolve_address_version(ip_address, ip_version)
    async with _kea_lease_client() as client:
        delete_payload = await client.delete_lease(str(ip_address), version=ip_version)
        if not lease_deleted(delete_payload, ip_version=ip_version):
            raise HTTPException(status_code=404, detail=f"Lease {ip_address} was not found")
        return Response(status_code=204)


@app.get("/summary", response_model=DhcpSummaryResponse)
async def get_summary(
    request: Request,
    ip_version: IpVersion = IpVersion.V4,
) -> DhcpSummaryResponse:
    """Return lease, reservation, and pool counts."""
    async with _kea_lease_client() as client:
        config, statistics = await _fetch_summary_sources(
            client,
            ip_version,
        )
        return build_dhcp_summary(
            config,
            statistics,
            ip_version=ip_version,
        )


@app.delete("/admin/cache")
async def flush_cache(request: Request, ip_version: int = 4) -> dict[str, str]:
    """Flush the cached KEA DHCP configuration from Redis."""
    app_config = load_config()
    redis_client = RedisClient.from_config(app_config)
    try:
        deleted = await redis_client.flush_kea_config(ip_version)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"No cached configuration found for DHCPv{ip_version}",
            )
        return {"detail": f"DHCPv{ip_version} cached configuration flushed"}
    finally:
        await redis_client.close()


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus metrics for cache observability.

    We handle this endpoint ourselves rather than via instrumentator.expose()
    because we need async Redis reads to update the cache refresh gauge before
    generating the response.
    """
    app_config = load_config()
    redis_client = RedisClient.from_config(app_config)
    try:
        for ip_version in (4, 6):
            timestamp = await redis_client.load_refresh_timestamp(ip_version)
            if timestamp is not None:
                CACHE_LAST_REFRESH.labels(ip_version=str(ip_version)).set(timestamp)
    finally:
        await redis_client.close()
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


async def _assert_kea_online(client: KeaClient) -> None:
    """Raise 500 unless the Kea DHCP process/control channel is alive.

    This is the *liveness* signal: it only reflects whether Kea itself is
    running and answering on its control channel. It deliberately says nothing
    about whether the desired configuration has been applied, so a config
    mismatch never restarts a live Kea.
    """
    status = await client.status()
    for process in status:
        if process["result"] != 0:
            raise HTTPException(status_code=500, detail=status)


def _assert_desired_config_applied(
    config: list[dict[str, Any]] | dict[str, Any],
    app_config: ConfigParser,
) -> None:
    """Raise 500 unless Kea's running config reflects the desired configuration.

    This is the *readiness* gate. It is intentionally strict: a freshly started
    Kea running only the bootstrap config must NOT be reported ready, otherwise
    an unconfigured pod could become an external DHCP target that silently drops
    requests. There is deliberately no "empty Redis" escape hatch here -- the
    bootstrap/validation path reaches Kea through the internal validation Service
    (publishNotReadyAddresses), not by weakening readiness.

    Today the applied-config signal is a proxy: on remote-lease-db deployments a
    successful initial sync swaps Kea's ``lease-database`` from the bootstrap
    ``memfile`` to ``postgresql``, so its presence means "the desired config was
    applied".

    TODO(part-1 hash verification): once ``KeaClient.get_config_hash`` /
    ``expected_hash`` land, plug the applied-hash-vs-expected-hash comparison in
    here (in addition to / in place of the lease-database heuristic) so readiness
    reflects the exact desired configuration rather than a proxy signal.
    """
    # KEA returns a list of responses, we want the first one
    config_list = config if isinstance(config, list) else [config]

    # Some error conditions don't return result or argument keys
    if config_list[0].get("result", 0) != 0 or "arguments" not in config_list[0]:
        error_msg = config_list[0].get("text", "No message provided")
        raise HTTPException(status_code=500, detail=f"Failed to get KEA config: {error_msg}")

    # Only remote (PostgreSQL) lease-db deployments have a config-applied proxy
    # signal in the running Kea config. Local/memfile deployments have no such
    # marker, so there is nothing further to assert here. Every chart-rendered
    # INI hardcodes ``local = false``, so no deployed pod takes this branch; the
    # hash comparison in the TODO above is what closes it for hand-written INIs.
    remote_lease_db = not app_config["dhcp.lease_db"].getboolean("local")
    if not remote_lease_db:
        return

    dhcp4_config = config_list[0]["arguments"]["Dhcp4"]
    lease_db_type = dhcp4_config.get("lease-database", {}).get("type", "memfile")
    if lease_db_type != "postgresql":
        raise HTTPException(status_code=500, detail="Lease database not present in Dhcp4 config")


@app.get("/livez")
async def livez() -> str:
    """Liveness probe for the Kea container.

    Only checks that the Kea DHCP process/control channel is alive. A config
    mismatch (desired config not yet applied) must NOT fail this probe, because
    liveness failures recycle the Kea container; config correctness is enforced
    separately by the readiness probe (``/healthcheck``).
    """
    client = KeaClient.from_config(attached=True)
    try:
        await _assert_kea_online(client)
        return "OK"
    except TimeoutError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ClientResponseError as exc:
        raise HTTPException(status_code=500, detail=str(exc.message)) from exc
    finally:
        await client.close()


@app.get("/healthcheck")
async def healthcheck() -> str:
    """Readiness probe (strict): Kea online AND desired config applied.

    Gates both pod readiness and the externally exposed NLB health endpoint. A
    pod becomes ready -- and therefore a valid external DHCP target -- only when
    Kea is online AND the expected desired configuration has been successfully
    applied/verified. An unconfigured pod (bootstrap config only) is never ready.

    A config mismatch makes the pod UNREADY (triggering reconciliation) but does
    NOT restart a live Kea; the readiness path never recycles the Kea container.
    """
    client = KeaClient.from_config(attached=True)
    app_config = load_config()
    try:
        await _assert_kea_online(client)
        config = await client.get_config(4)
        _assert_desired_config_applied(config, app_config)
        return "OK"
    except TimeoutError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ClientResponseError as exc:
        raise HTTPException(status_code=500, detail=str(exc.message)) from exc
    finally:
        await client.close()


install_identity_probe(app)
