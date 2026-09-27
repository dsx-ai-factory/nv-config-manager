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
"""Freeze core activity contracts across their package extraction."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast, get_type_hints

from temporalio import activity

from nv_config_manager.temporal.common.activities import REGISTERED_COMMON_ACTIVITIES
from nv_config_manager.temporal.hello_world.activities import (
    REGISTERED_ACTIVITIES as HELLO_WORLD_ACTIVITIES,
)
from nv_config_manager.temporal.hello_world.activities import (
    hello_world as hello_world_activities,
)
from nv_config_manager.temporal.ngc.activities import REGISTERED_ACTIVITIES as NGC_ACTIVITIES
from nv_config_manager.temporal.ngc.activities import config as config_activities
from nv_config_manager.temporal.ngc.activities import nats as nats_activities
from nv_config_manager.temporal.ngc.activities import slack as slack_activities
from nv_config_manager_workflows.activities import config as package_config_activities
from nv_config_manager_workflows.activities import hello_world as package_hello_world_activities
from nv_config_manager_workflows.activities import lock as package_lock_activities
from nv_config_manager_workflows.activities import nats as package_nats_activities
from nv_config_manager_workflows.activities import slack as package_slack_activities
from nv_config_manager_workflows.registration.contract import activity_name

_REGISTERED_TYPES_FIXTURE = Path(__file__).with_name("fixtures") / "registered_type_names.json"
_CORE_ACTIVITY_NAMES = {
    "get_ui_base_url",
    "hello_world_activity",
    "hello_world_prompt_activity",
    "hello_world_reject_activity",
    "publish_nats",
    "send_slack_message",
}
_EXPECTED_ACTIVITY_CONTRACTS = {
    "get_ui_base_url": {
        "async": False,
        "parameters": (),
        "return": "str",
    },
    "hello_world_activity": {
        "async": True,
        "parameters": (("name", "POSITIONAL_OR_KEYWORD", "required", "str"),),
        "return": "str",
    },
    "hello_world_prompt_activity": {
        "async": True,
        "parameters": (),
        "return": "str",
    },
    "hello_world_reject_activity": {
        "async": True,
        "parameters": (),
        "return": "str",
    },
    "publish_nats": {
        "async": True,
        "parameters": (
            ("activity_input", "POSITIONAL_OR_KEYWORD", "required", "PublishNatsInput"),
        ),
        "return": "None",
    },
    "send_slack_message": {
        "async": True,
        "parameters": (("input", "POSITIONAL_OR_KEYWORD", "required", "SlackMessageInput"),),
        "return": "SlackMessageOutput",
    },
}
_EXPECTED_MODEL_SCHEMAS = {
    "PublishNatsInput": {
        "description": "Input for publish activity.",
        "properties": {
            "subject": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Subject",
            },
            "message": {"title": "Message", "type": "string"},
        },
        "required": ["message"],
        "title": "PublishNatsInput",
        "type": "object",
    },
    "SlackMessageInput": {
        "description": "Slack message input.",
        "properties": {
            "message": {"title": "Message", "type": "string"},
            "thread_ts": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Thread Ts",
            },
            "link_workflow": {
                "default": False,
                "title": "Link Workflow",
                "type": "boolean",
            },
        },
        "required": ["message"],
        "title": "SlackMessageInput",
        "type": "object",
    },
    "SlackMessageOutput": {
        "description": "Slack message output.",
        "properties": {
            "thread_ts": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Thread Ts",
            }
        },
        "title": "SlackMessageOutput",
        "type": "object",
    },
}


def _annotation_name(annotation: Any) -> str:
    """Return a module-independent representation of a type annotation."""
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", str(annotation))


def _callable_contract(callable_: Any) -> dict[str, Any]:
    """Describe the callable without freezing its implementation module path."""
    signature = inspect.signature(callable_)
    type_hints = get_type_hints(callable_)
    parameters = tuple(
        (
            parameter.name,
            parameter.kind.name,
            "required" if parameter.default is inspect.Parameter.empty else parameter.default,
            _annotation_name(type_hints.get(parameter.name, parameter.annotation)),
        )
        for parameter in signature.parameters.values()
    )
    return {
        "async": inspect.iscoroutinefunction(callable_),
        "parameters": parameters,
        "return": _annotation_name(type_hints.get("return", signature.return_annotation)),
    }


def _registered_activity_names() -> list[str]:
    """Return the sorted set of activity type names registered by the worker."""
    names = [
        activity_name(cast(Callable[..., Any], registered))
        for registered in [
            *NGC_ACTIVITIES,
            *HELLO_WORLD_ACTIVITIES,
            *REGISTERED_COMMON_ACTIVITIES,
        ]
    ]
    assert all(name is not None for name in names)
    return sorted(name for name in names if name is not None)


def test_core_activity_names_signatures_and_execution_modes_are_frozen() -> None:
    """The move must preserve the names and callable forms workflows invoke."""
    callables = (
        hello_world_activities.hello_world_activity,
        hello_world_activities.hello_world_prompt_activity,
        hello_world_activities.hello_world_reject_activity,
        config_activities.get_ui_base_url,
        nats_activities.publish_nats,
        slack_activities.send_slack_message,
    )

    actual = {
        activity._Definition.must_from_callable(callable_).name: _callable_contract(callable_)
        for callable_ in callables
    }

    assert actual == _EXPECTED_ACTIVITY_CONTRACTS


def test_core_activity_model_schemas_and_default_dumps_are_frozen() -> None:
    """Temporal payload models retain their field names, types, and defaults."""
    models = (
        nats_activities.PublishNatsInput,
        slack_activities.SlackMessageInput,
        slack_activities.SlackMessageOutput,
    )

    assert {model.__name__: model.model_json_schema() for model in models} == (
        _EXPECTED_MODEL_SCHEMAS
    )
    assert nats_activities.PublishNatsInput(message="payload").model_dump() == {
        "subject": None,
        "message": "payload",
    }
    assert slack_activities.SlackMessageInput(message="hello").model_dump() == {
        "message": "hello",
        "thread_ts": None,
        "link_workflow": False,
    }
    assert slack_activities.SlackMessageOutput().model_dump() == {"thread_ts": None}


def test_core_package_activity_surface_matches_frozen_contracts() -> None:
    """New package modules expose all six activities through immutable domain tuples."""
    domain_tuples = (
        package_hello_world_activities.HELLO_WORLD_ACTIVITIES,
        package_config_activities.CONFIG_ACTIVITIES,
        package_nats_activities.NATS_ACTIVITIES,
        package_slack_activities.SLACK_ACTIVITIES,
    )
    callables = tuple(callable_ for domain in domain_tuples for callable_ in domain)

    assert all(isinstance(domain, tuple) for domain in domain_tuples)
    assert {
        activity._Definition.must_from_callable(callable_).name: _callable_contract(callable_)
        for callable_ in callables
    } == _EXPECTED_ACTIVITY_CONTRACTS
    assert {
        model.__name__: model.model_json_schema()
        for model in (
            package_nats_activities.PublishNatsInput,
            package_slack_activities.SlackMessageInput,
            package_slack_activities.SlackMessageOutput,
        )
    } == _EXPECTED_MODEL_SCHEMAS


def test_core_legacy_paths_export_canonical_objects() -> None:
    """Compatibility modules re-export package objects without redecorating them."""
    assert (
        hello_world_activities.hello_world_activity
        is package_hello_world_activities.hello_world_activity
    )
    assert (
        hello_world_activities.hello_world_prompt_activity
        is package_hello_world_activities.hello_world_prompt_activity
    )
    assert (
        hello_world_activities.hello_world_reject_activity
        is package_hello_world_activities.hello_world_reject_activity
    )
    assert config_activities.get_ui_base_url is package_config_activities.get_ui_base_url
    assert config_activities.build_workflow_url is package_config_activities.build_workflow_url
    assert nats_activities.publish_nats is package_nats_activities.publish_nats
    assert nats_activities.PublishNatsInput is package_nats_activities.PublishNatsInput
    assert slack_activities.send_slack_message is package_slack_activities.send_slack_message
    assert slack_activities.SlackMessageInput is package_slack_activities.SlackMessageInput
    assert slack_activities.SlackMessageOutput is package_slack_activities.SlackMessageOutput
    assert isinstance(HELLO_WORLD_ACTIVITIES, list)
    assert HELLO_WORLD_ACTIVITIES == list(package_hello_world_activities.HELLO_WORLD_ACTIVITIES)
    assert isinstance(REGISTERED_COMMON_ACTIVITIES, list)
    assert REGISTERED_COMMON_ACTIVITIES == list(package_lock_activities.LOCK_ACTIVITIES)


def test_core_registered_activity_baseline_is_frozen_at_121_unique_names() -> None:
    """Do not silently resolve the ticket's 120-versus-121 discrepancy."""
    expected = json.loads(_REGISTERED_TYPES_FIXTURE.read_text())["activities"]
    actual = _registered_activity_names()

    assert len(expected) == len(set(expected)) == 121
    assert actual == expected
    assert _CORE_ACTIVITY_NAMES <= set(actual)
