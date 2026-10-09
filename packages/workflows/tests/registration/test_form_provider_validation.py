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

import sys
from enum import StrEnum
from types import ModuleType
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration.form_catalog import FormOptionProviderBinding
from nv_config_manager_workflows.registration.form_provider_validation import (
    resolve_form_option_provider,
)
from nv_config_manager_workflows.ui import (
    Dependency,
    FormOptionProvider,
    FormOptionSource,
    OptionSourceResponse,
    WorkflowFormContractError,
)

MODULE_NAME = "nvcm_form_provider_validation_fixture"


class Workflow(WorkflowMetadataMixin):
    pass


class Mode(StrEnum):
    FAST = "fast"
    SAFE = "safe"


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str
    modes: list[Mode] = Field(default_factory=list)
    limit: int | None = None
    format: Literal["short", "long"] = "short"


async def resolver(query: Query) -> OptionSourceResponse:
    return OptionSourceResponse(items=[])


@pytest.fixture(autouse=True)
def provider_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType(MODULE_NAME)
    setattr(module, "Query", Query)
    setattr(module, "resolver", resolver)
    monkeypatch.setitem(sys.modules, MODULE_NAME, module)


def binding(
    *,
    query_model: str = "Query",
    resolver_name: str = "resolver",
    uses: tuple[FormOptionSource, ...] = (
        FormOptionSource("profiles", depends_on={"site": Dependency("site")}),
    ),
) -> FormOptionProviderBinding:
    return FormOptionProviderBinding(
        workflow=Workflow,
        workflow_form_id="workflow",
        plugin="fixture",
        source="profiles",
        endpoint="/v1/workflow/workflow/form-options/profiles",
        declaration=FormOptionProvider(
            resolver=f"{MODULE_NAME}:{resolver_name}",
            query_model=f"{MODULE_NAME}:{query_model}",
        ),
        uses=uses,
    )


def test_resolves_a_strict_flat_query_model_and_async_resolver() -> None:
    resolved = resolve_form_option_provider(binding())

    assert resolved.binding.source == "profiles"
    assert resolved.query_model is Query
    assert resolved.resolver is resolver


@pytest.mark.parametrize(
    ("query_model", "message"),
    [
        (
            type("AllowsExtra", (BaseModel,), {"__annotations__": {"site": str}}),
            "extra='forbid'",
        ),
        (
            type(
                "Aliased",
                (BaseModel,),
                {
                    "__annotations__": {"site": str},
                    "model_config": ConfigDict(extra="forbid"),
                    "site": Field(alias="site-id"),
                },
            ),
            "must not declare aliases",
        ),
        (
            type(
                "Nested",
                (BaseModel,),
                {
                    "__annotations__": {"filters": dict[str, str]},
                    "model_config": ConfigDict(extra="forbid"),
                },
            ),
            "must be a scalar",
        ),
        (
            type(
                "Secret",
                (BaseModel,),
                {
                    "__annotations__": {"password": SecretStr},
                    "model_config": ConfigDict(extra="forbid"),
                },
            ),
            "must be a scalar",
        ),
    ],
)
def test_rejects_query_models_outside_the_public_get_contract(
    monkeypatch: pytest.MonkeyPatch,
    query_model: type[BaseModel],
    message: str,
) -> None:
    module = sys.modules[MODULE_NAME]
    monkeypatch.setattr(module, "InvalidQuery", query_model, raising=False)

    with pytest.raises(WorkflowFormContractError, match=message):
        resolve_form_option_provider(binding(query_model="InvalidQuery"))


def test_rejects_a_sync_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    def sync_resolver(query: Query) -> OptionSourceResponse:
        return OptionSourceResponse(items=[])

    monkeypatch.setattr(sys.modules[MODULE_NAME], "sync_resolver", sync_resolver, raising=False)

    with pytest.raises(WorkflowFormContractError, match="must be an async function"):
        resolve_form_option_provider(binding(resolver_name="sync_resolver"))


def test_rejects_a_resolver_with_the_wrong_annotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def wrong_resolver(query: BaseModel) -> OptionSourceResponse:
        return OptionSourceResponse(items=[])

    monkeypatch.setattr(sys.modules[MODULE_NAME], "wrong_resolver", wrong_resolver, raising=False)

    with pytest.raises(WorkflowFormContractError, match="must be annotated as Query"):
        resolve_form_option_provider(binding(resolver_name="wrong_resolver"))


@pytest.mark.parametrize("annotation", [None, list[str]])
def test_rejects_a_resolver_without_the_exact_response_annotation(
    monkeypatch: pytest.MonkeyPatch,
    annotation: object,
) -> None:
    async def wrong_return(query: Query) -> object:
        return OptionSourceResponse(items=[])

    if annotation is None:
        wrong_return.__annotations__.pop("return")
    else:
        wrong_return.__annotations__["return"] = annotation
    monkeypatch.setattr(sys.modules[MODULE_NAME], "wrong_return", wrong_return, raising=False)

    with pytest.raises(
        WorkflowFormContractError,
        match="return must be annotated as OptionSourceResponse",
    ):
        resolve_form_option_provider(binding(resolver_name="wrong_return"))


def test_every_use_must_supply_required_fields() -> None:
    with pytest.raises(WorkflowFormContractError, match=r"required query fields: \['site'\]"):
        resolve_form_option_provider(binding(uses=(FormOptionSource("profiles"),)))


def test_optional_dependency_does_not_supply_a_required_query_field() -> None:
    with pytest.raises(WorkflowFormContractError, match=r"required query fields: \['site'\]"):
        resolve_form_option_provider(
            binding(
                uses=(
                    FormOptionSource(
                        "profiles",
                        depends_on={"site": Dependency("site", required=False)},
                    ),
                )
            )
        )


def test_rejects_unknown_query_fields() -> None:
    with pytest.raises(WorkflowFormContractError, match=r"unknown query fields: \['tenant'\]"):
        resolve_form_option_provider(
            binding(
                uses=(
                    FormOptionSource(
                        "profiles",
                        params={"tenant": "tenant-a"},
                        depends_on={"site": Dependency("site")},
                    ),
                )
            )
        )


def test_rejects_an_invalid_static_query_value() -> None:
    with pytest.raises(
        WorkflowFormContractError,
        match="invalid static value for query field 'format'",
    ):
        resolve_form_option_provider(
            binding(
                uses=(
                    FormOptionSource(
                        "profiles",
                        params={"format": "invalid"},
                        depends_on={"site": Dependency("site")},
                    ),
                )
            )
        )


def test_a_scalar_static_value_can_supply_one_repeated_query_value() -> None:
    resolve_form_option_provider(
        binding(
            uses=(
                FormOptionSource(
                    "profiles",
                    params={"modes": "fast"},
                    depends_on={"site": Dependency("site")},
                ),
            )
        )
    )


def test_rejects_an_empty_static_query_list() -> None:
    with pytest.raises(WorkflowFormContractError, match="query field 'modes' must not be empty"):
        resolve_form_option_provider(
            binding(
                uses=(
                    FormOptionSource(
                        "profiles",
                        params={"modes": []},
                        depends_on={"site": Dependency("site")},
                    ),
                )
            )
        )
