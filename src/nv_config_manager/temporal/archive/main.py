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
"""Temporal archive."""

import argparse
import base64
import gzip
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from elasticsearch import Elasticsearch
from nats.aio.msg import Msg
from nv_config_manager_infrastructure.nats.admin import ARCHIVE_CONSUMER_MAX_DELIVER
from requests_aws4auth import AWS4Auth  # type: ignore[import-untyped]

from nv_config_manager.common.client.nvdataflow import NVDataflowClient
from nv_config_manager.common.config.loader import load_config
from nv_config_manager.common.config.nats import nats_archive_config
from nv_config_manager.common.log import configure_logging
from nv_config_manager.temporal.api.workflow_v1 import WorkflowDetailResponse, get_client
from nv_config_manager.temporal.client.nats import NatsConsumer
from nv_config_manager.temporal.telemetry import setup_telemetry

config = load_config()

ARCHIVE_BACKEND = (
    "nvdataflow"
    if "temporal.nvdataflow" in config and config["temporal.nvdataflow"].get("project")
    else "elasticsearch"
)

ELASTICSEARCH_INDEX_PREFIX = "workflow_results"
ELASTICSEARCH_INDEX_MAPPING = {
    "dynamic": "false",
    "properties": {
        "@timestamp": {"type": "date"},
        "start_time": {"type": "date"},
        "close_time": {"type": "date"},
        "started_by": {"type": "keyword"},
        "status": {"type": "keyword"},
        "config_manager_temporal_server": {"type": "keyword"},
        "workflow_type": {"type": "keyword"},
        "search_attributes": {"type": "object"},
    },
}


ARCHIVE_RETRY_DELAY_SECONDS = 300
NVDATAFLOW_SUCCESS_STATUSES = frozenset({201, 202})
NVDATAFLOW_REJECTED_STATUSES = frozenset({400, 413})


class ArchiveRejectedError(Exception):
    """The archive backend refused the record itself, so retrying cannot help."""


async def handle_archive_msg(msg: Msg, max_deliver: int = ARCHIVE_CONSUMER_MAX_DELIVER) -> None:
    """Archive one workflow result, then ack, retry, or discard its message.

    A failed write goes back to JetStream with a delay instead of being acked,
    so an outage leaves the backlog in the stream rather than dropping records.
    ``max_deliver`` is the bound durable's limit; ``-1`` retries indefinitely.
    """
    try:
        data = json.loads(msg.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logging.exception("Discarding unreadable archive message")
        await msg.term()
        return
    if not isinstance(data, dict):
        logging.error("Discarding archive message whose payload is not a JSON object")
        await msg.term()
        return
    logging.info("Received archive event: %s", data)
    if not data.get("workflow_id"):
        logging.error("No workflow ID in archive message: %s", data)
        await msg.ack()
        return

    workflow_id = data["workflow_id"]
    try:
        await _archive_workflow(data)
    except ArchiveRejectedError:
        logging.exception("Archive backend rejected workflow %s; discarding it", workflow_id)
        await msg.term()
        return
    except Exception:
        deliveries = msg.metadata.num_delivered
        if max_deliver > 0 and deliveries >= max_deliver:
            logging.exception(
                "Giving up on archiving workflow %s after %d deliveries",
                workflow_id,
                deliveries,
            )
            await msg.term()
            return
        logging.exception(
            "Failed to archive workflow %s on delivery %d; retrying in %ds",
            workflow_id,
            deliveries,
            ARCHIVE_RETRY_DELAY_SECONDS,
        )
        await msg.nak(delay=ARCHIVE_RETRY_DELAY_SECONDS)
        return
    await msg.ack()


async def _archive_workflow(data: dict[str, Any]) -> None:
    """Look up the finished workflow in Temporal and write it to the archive backend."""
    temporal_client = await get_client()
    workflow_handle = temporal_client.get_workflow_handle(data["workflow_id"])
    workflow_response = await WorkflowDetailResponse.from_handle(workflow_handle)
    workflow_details = workflow_response.model_dump(mode="json")

    logging.debug("Response from temporal API: %s", workflow_details)

    if ARCHIVE_BACKEND == "nvdataflow":
        await _archive_nvdataflow(workflow_details)
    else:
        workflow_details["@timestamp"] = data["publish_time"]
        _archive_elasticsearch(workflow_details)


async def _archive_nvdataflow(workflow_details: Any) -> None:
    """Archive workflow details to nvdataflow."""
    compressed = gzip.compress(json.dumps(workflow_details).encode("utf-8"))
    encoded = base64.b64encode(compressed).decode("utf-8")

    archive_data = {
        "ts_created": int(datetime.now(UTC).timestamp() * 1000),
        "ts_start_time": int(
            datetime.fromisoformat(workflow_details["start_time"]).timestamp() * 1000
        ),
        "s_started_by": workflow_details["started_by"],
        "s_status": workflow_details["status"],
        "s_config_manager_temporal_server": config["temporal"]["api_url"],
        "s_workflow_id": workflow_details["id"],
        "s_workflow_type": workflow_details["workflow_type"],
        "obj_search_attributes": workflow_details["search_attributes"],
        "ni_body": encoded,
    }

    # Handle close_time (now a string if it exists)
    close_time_str = workflow_details.get("close_time")
    if close_time_str is not None:
        archive_data["ts_close_time"] = int(
            datetime.fromisoformat(close_time_str).timestamp() * 1000
        )

    nvdataflow_config = config["temporal.nvdataflow"]
    async with NVDataflowClient(
        project=nvdataflow_config["project"],
        sync=nvdataflow_config.getboolean("sync", fallback=False),
        endpoint=nvdataflow_config.get("endpoint", fallback=None),
        async_endpoint=nvdataflow_config.get("async_endpoint", fallback=None),
        sync_endpoint=nvdataflow_config.get("sync_endpoint", fallback=None),
    ) as client:
        res_status_code = await client.post(data=archive_data)

    if res_status_code in NVDATAFLOW_REJECTED_STATUSES:
        raise ArchiveRejectedError(f"nvdataflow rejected the record with status {res_status_code}")
    if res_status_code not in NVDATAFLOW_SUCCESS_STATUSES:
        raise RuntimeError(
            f"Failed to archive workflow details to nvdataflow with status code {res_status_code}"
        )
    logging.info("Results written to nvdataflow. Archive handler complete")


def _archive_elasticsearch(workflow_details: Any) -> None:
    """Archive workflow details to Elasticsearch."""
    # Add temporal server so we can track which environment sourced the workflow
    workflow_details["config_manager_temporal_server"] = config["temporal"]["api_url"]

    elastic = None
    if config["temporal.elasticsearch"]["local"] == "true":
        elastic = Elasticsearch(
            config["temporal.elasticsearch"]["server"],
            basic_auth=(
                config["temporal.elasticsearch"]["user"],
                config["temporal.elasticsearch"]["password"],
            ),
        )
    else:
        # boto3 auth uses env variables injected by EKS (AWS_ROLE_ARN, etc..)
        # For AWS Elasticsearch/OpenSearch, use requests-based auth
        session = boto3.Session()
        creds = session.get_credentials()  # type: ignore[attr-defined]
        # Elasticsearch 8.x uses headers for AWS auth instead of http_auth

        awsauth = AWS4Auth(
            creds.access_key,
            creds.secret_key,
            session.region_name,  # type: ignore[attr-defined]
            "es",
            session_token=creds.token,
        )
        # Use the requests transport for AWS authentication
        elastic = Elasticsearch(
            config["temporal.elasticsearch"]["server"],
            http_auth=awsauth,  # type: ignore[arg-type]
            verify_certs=True,
        )

    logging.debug("Connected to ElasticSearch: %s", elastic.info())
    index = f"{ELASTICSEARCH_INDEX_PREFIX}_{workflow_details['workflow_type'].lower()}"
    if not elastic.indices.exists(index=index):
        logging.info("Creating ElasticSearch index: %s", index)
        elastic.indices.create(
            index=index,
            mappings=ELASTICSEARCH_INDEX_MAPPING,
        )
    elastic.index(
        index=index,
        document=workflow_details,
    )
    logging.info("Results written to elasticsearch. Archive handler complete")


def main() -> None:
    """Run the temporal archive."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d",
        "--debug",
        help="Print lots of debugging statements",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
        default=logging.WARNING,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Be verbose",
        action="store_const",
        dest="loglevel",
        const=logging.INFO,
    )
    args = parser.parse_args()
    log_level: int = args.loglevel
    os.environ.setdefault(
        "LOG_LEVEL",
        logging.getLevelName(log_level),
    )
    configure_logging(service="temporal-archive")
    setup_telemetry("nv-config-manager-temporal-archive")

    stream, subject = nats_archive_config()

    async def handler(msg: Msg) -> None:
        await handle_archive_msg(msg, consumer.max_deliver)

    consumer = NatsConsumer(
        stream=stream,
        subject=subject,
        queue_suffix="archive",
        handler=handler,
        manual_ack=True,
    )
    consumer.run()


if __name__ == "__main__":
    main()
