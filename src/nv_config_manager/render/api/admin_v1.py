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
"""V1 Admin API Endpoints for NATS Consumer Management."""

from enum import StrEnum
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from nats.aio.client import Client
from nats.js import JetStreamContext
from nats.js.errors import NotFoundError
from pydantic import BaseModel

from nv_config_manager.common.config import (
    DEFAULT_NATS_API_PREFIX,
    load_config,
    nats_config_manager_api_prefix,
    nats_connection,
    nats_dcim_change_config,
    nats_nautobot_api_prefix,
    nats_render_change_config,
)
from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.common.nats_admin import (
    is_nats_permissions_error,
    reset_consumer_request,
)


class ConsumerType(StrEnum):
    """Valid consumer types for NATS consumer management."""

    dcim = "dcim"
    device = "device"
    template = "template"


responses: dict[int | str, dict[str, Any]] = {
    200: {"description": "Operation successful"},
    500: {"description": "Internal server error"},
}

permission_response: dict[int | str, dict[str, Any]] = {
    403: {"description": "NATS account lacks the required consumer permission"}
}
consumer_lookup_responses: dict[int | str, dict[str, Any]] = {
    **permission_response,
    404: {"description": "Consumer not found"},
}

router = APIRouter(prefix="/admin", responses=responses)
logger = get_logger(__name__, category=LogCategory.RENDER_API)


class ConsumerInfo(BaseModel):
    """Consumer information model."""

    name: str
    stream: str
    subject: str
    num_pending: int
    num_ack_pending: int
    num_delivered: int


class ConsumerResetResponse(BaseModel):
    """Consumer reset response model."""

    consumer_name: str
    stream: str
    status: str
    message: str


class ConsumerListResponse(BaseModel):
    """Consumer list response model."""

    consumers: list[ConsumerInfo]


def get_consumer_configs() -> dict[str, dict[str, str]]:
    """Get the consumer configurations mapped by consumer type."""
    config = load_config()
    nats_config = config["nats"]

    dcim_stream, dcim_subject = nats_dcim_change_config(config)
    dcim_api_prefix = nats_nautobot_api_prefix(config)
    render_stream, render_subject = nats_render_change_config(config)
    render_api_prefix = nats_config_manager_api_prefix(config)

    return {
        "dcim": {
            "durable_name": nats_config.get("nautobot_consumer_name", "nv-config-manager-nautobot"),
            "stream": dcim_stream,
            "subject": dcim_subject,
            "api_prefix": dcim_api_prefix,
        },
        "device": {
            "durable_name": nats_config.get(
                "config_manager_consumer_name", "nv-config-manager-device"
            ),
            "stream": render_stream,
            "subject": render_subject,
            "api_prefix": render_api_prefix,
        },
    }


def jetstream_for_consumer(nats_conn: Client, consumer_config: dict[str, str]) -> JetStreamContext:
    """Return a JetStream context for this consumer's configured stream account."""
    return nats_conn.jetstream(prefix=consumer_config.get("api_prefix", DEFAULT_NATS_API_PREFIX))


async def nats_connection_with_permission_tracking() -> tuple[Client, list[Exception]]:
    """Connect while retaining asynchronous NATS permission violations."""
    permission_errors: list[Exception] = []

    async def error_cb(exc: Exception) -> None:
        if is_nats_permissions_error(exc):
            permission_errors.append(exc)
        else:
            logger.error("Unhandled NATS error", exc_info=exc)

    return await nats_connection(error_cb=error_cb), permission_errors


def operation_was_denied(exc: Exception, permission_errors: list[Exception]) -> bool:
    """Include permission violations delivered through the NATS error callback."""
    return is_nats_permissions_error(exc) or bool(permission_errors)


@router.get("/consumers", response_model=ConsumerListResponse)
async def list_consumers(request: Request) -> ConsumerListResponse:
    """List all consumers and their current status."""
    nats_conn: Client | None = None
    try:
        nats_conn, permission_errors = await nats_connection_with_permission_tracking()
        consumer_configs = get_consumer_configs()
        consumers = []

        for config in consumer_configs.values():
            jetstream = jetstream_for_consumer(nats_conn, config)
            permission_errors.clear()
            try:
                consumer_info = await jetstream.consumer_info(
                    stream=config["stream"], consumer=config["durable_name"]
                )

                consumers.append(
                    ConsumerInfo(
                        name=config["durable_name"],
                        stream=config["stream"],
                        subject=config["subject"],
                        num_pending=consumer_info.num_pending,
                        num_ack_pending=consumer_info.num_ack_pending,
                        num_delivered=consumer_info.delivered.consumer_seq,
                    )
                )

            except Exception as e:
                if operation_was_denied(e, permission_errors):
                    logger.error(
                        "NATS denied consumer lookup. Ask the NATS administrator to grant "
                        "publish access to %s",
                        f"{config['api_prefix']}.CONSUMER.INFO."
                        f"{config['stream']}.{config['durable_name']}",
                    )
                else:
                    logger.warning(
                        "Could not get info for consumer %s: %s", config["durable_name"], e
                    )
                # Add consumer with unknown status
                consumers.append(
                    ConsumerInfo(
                        name=config["durable_name"],
                        stream=config["stream"],
                        subject=config["subject"],
                        num_pending=-1,
                        num_ack_pending=-1,
                        num_delivered=-1,
                    )
                )

        return ConsumerListResponse(consumers=consumers)

    except Exception as exc:
        logger.exception("Error listing consumers")
        raise HTTPException(status_code=500, detail="Failed to list consumers") from exc
    finally:
        if nats_conn is not None:
            await nats_conn.close()


@router.delete(
    "/consumers/{consumer_type}/reset",
    response_model=ConsumerResetResponse,
    responses=permission_response,
)
async def reset_consumer(consumer_type: ConsumerType, request: Request) -> ConsumerResetResponse:
    """Delete a consumer so the running service recreates it at the stream head."""

    try:
        consumer_configs = get_consumer_configs()
        config = consumer_configs[consumer_type]

        nats_conn, permission_errors = await nats_connection_with_permission_tracking()
        try:
            jetstream = jetstream_for_consumer(nats_conn, config)

            pending_msgs: int | None = 0
            permission_errors.clear()
            try:
                consumer_info = await jetstream.consumer_info(
                    stream=config["stream"], consumer=config["durable_name"]
                )
                pending_msgs = consumer_info.num_pending
            except NotFoundError:
                logger.info("Consumer %s was already absent", config["durable_name"])
            except Exception as e:
                if operation_was_denied(e, permission_errors):
                    logger.warning(
                        "NATS denied backlog inspection for consumer %s; attempting the reset "
                        "with an unknown pending-message count",
                        config["durable_name"],
                    )
                    pending_msgs = None
                else:
                    raise

            permission_errors.clear()
            try:
                await jetstream.delete_consumer(
                    stream=config["stream"], consumer=config["durable_name"]
                )
                backlog_message = (
                    f"Had {pending_msgs} pending messages."
                    if pending_msgs is not None
                    else "The pending-message count was unavailable."
                )
                message = (
                    f"Consumer '{config['durable_name']}' deleted successfully. "
                    f"{backlog_message} The running consumer will attempt to recreate it at "
                    "the stream head; if create permission is not granted, ask the NATS "
                    "administrator to provision it."
                )
            except NotFoundError:
                message = (
                    f"Consumer '{config['durable_name']}' was already deleted or did not exist."
                )
            except Exception as e:
                if operation_was_denied(e, permission_errors):
                    detail = reset_consumer_request(
                        config["api_prefix"],
                        config["stream"],
                        config["durable_name"],
                        config["subject"],
                    )
                    logger.error("NATS denied consumer reset. %s", detail)
                    raise HTTPException(status_code=403, detail=detail) from e
                raise

            return ConsumerResetResponse(
                consumer_name=config["durable_name"],
                stream=config["stream"],
                status="success",
                message=message,
            )
        finally:
            await nats_conn.close()

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error resetting consumer %s", consumer_type)
        raise HTTPException(status_code=500, detail="Failed to reset consumer") from exc


@router.delete("/consumers/reset-all", response_model=list[ConsumerResetResponse])
async def reset_all_consumers(request: Request) -> list[ConsumerResetResponse]:
    """Delete every consumer so the running services recreate them at the stream head."""
    try:
        results = []
        consumer_configs = get_consumer_configs()

        nats_conn, permission_errors = await nats_connection_with_permission_tracking()
        try:
            for consumer_type, config in consumer_configs.items():
                try:
                    jetstream = jetstream_for_consumer(nats_conn, config)
                    pending_msgs: int | None = 0
                    permission_errors.clear()
                    try:
                        consumer_info = await jetstream.consumer_info(
                            stream=config["stream"], consumer=config["durable_name"]
                        )
                        pending_msgs = consumer_info.num_pending
                    except NotFoundError:
                        logger.info("Consumer %s was already absent", config["durable_name"])
                    except Exception as e:
                        if operation_was_denied(e, permission_errors):
                            logger.warning(
                                "NATS denied backlog inspection for consumer %s; attempting "
                                "the reset with an unknown pending-message count",
                                config["durable_name"],
                            )
                            pending_msgs = None
                        else:
                            raise

                    permission_errors.clear()
                    try:
                        await jetstream.delete_consumer(
                            stream=config["stream"], consumer=config["durable_name"]
                        )
                        backlog_message = (
                            f"Had {pending_msgs} pending messages."
                            if pending_msgs is not None
                            else "The pending-message count was unavailable."
                        )
                        message = (
                            f"Consumer '{config['durable_name']}' deleted successfully. "
                            f"{backlog_message}"
                        )
                    except NotFoundError:
                        message = (
                            f"Consumer '{config['durable_name']}' was already deleted "
                            "or did not exist."
                        )
                    status = "success"

                except Exception as e:
                    status = "error"
                    if operation_was_denied(e, permission_errors):
                        message = reset_consumer_request(
                            config["api_prefix"],
                            config["stream"],
                            config["durable_name"],
                            config["subject"],
                        )
                        logger.error("NATS denied reset of consumer %s. %s", consumer_type, message)
                    else:
                        message = f"Failed to delete consumer '{config['durable_name']}': {str(e)}"
                        logger.error("Error processing consumer %s: %s", consumer_type, e)

                results.append(
                    ConsumerResetResponse(
                        consumer_name=config["durable_name"],
                        stream=config["stream"],
                        status=status,
                        message=message,
                    )
                )

            return results
        finally:
            await nats_conn.close()

    except Exception as exc:
        logger.exception("Error resetting all consumers")
        raise HTTPException(status_code=500, detail="Failed to reset consumers") from exc


@router.get(
    "/consumers/{consumer_type}",
    response_model=ConsumerInfo,
    responses=consumer_lookup_responses,
)
async def get_consumer_info(consumer_type: ConsumerType, request: Request) -> ConsumerInfo:
    """Get detailed information about a specific consumer."""

    try:
        consumer_configs = get_consumer_configs()
        config = consumer_configs[consumer_type]

        nats_conn, permission_errors = await nats_connection_with_permission_tracking()
        try:
            jetstream = jetstream_for_consumer(nats_conn, config)
            permission_errors.clear()
            consumer_info = await jetstream.consumer_info(
                stream=config["stream"], consumer=config["durable_name"]
            )

            result = ConsumerInfo(
                name=config["durable_name"],
                stream=config["stream"],
                subject=config["subject"],
                num_pending=consumer_info.num_pending,
                num_ack_pending=consumer_info.num_ack_pending,
                num_delivered=consumer_info.delivered.consumer_seq,
            )

        except NotFoundError as e:
            raise HTTPException(
                status_code=404,
                detail=f"Consumer '{config['durable_name']}' not found",
            ) from e
        except Exception as e:
            if operation_was_denied(e, permission_errors):
                detail = (
                    "Ask the NATS administrator to grant publish access to "
                    f"{config['api_prefix']}.CONSUMER.INFO."
                    f"{config['stream']}.{config['durable_name']}"
                )
                logger.error("NATS denied consumer lookup. %s", detail)
                raise HTTPException(status_code=403, detail=detail) from e
            raise
        finally:
            await nats_conn.close()

        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error getting consumer info for %s", consumer_type)
        raise HTTPException(status_code=500, detail="Failed to get consumer info") from exc
