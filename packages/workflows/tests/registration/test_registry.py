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

import dataclasses
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from temporalio import activity, workflow

from nv_config_manager_workflows.metadata import WorkflowMetadataMixin
from nv_config_manager_workflows.registration import registry as registry_module
from nv_config_manager_workflows.registration.builtin import BUILTIN_PLUGIN_NAME
from nv_config_manager_workflows.registration.descriptor import (
    UNKNOWN_PLUGIN_VERSION,
    WorkflowPluginDescriptor,
)
from nv_config_manager_workflows.registration.errors import (
    WorkflowConflictError,
    WorkflowRegistrationError,
)
from nv_config_manager_workflows.registration.form_catalog import (
    FormOptionProviderBinding,
    WorkflowFormCatalog,
    WorkflowFormDiagnostic,
    WorkflowFormWarning,
)
from nv_config_manager_workflows.registration.form_validation import validate_plugin_forms
from nv_config_manager_workflows.registration.registry import (
    PluginInfo,
    SchedulerRegistration,
    WorkflowRegistry,
)
from nv_config_manager_workflows.stage import StageMixin
from nv_config_manager_workflows.ui import (
    Dependency,
    FormOptionProvider,
    FormOptionSource,
    FormSchema,
    WorkflowFormContractError,
    api_options,
)


class DeviceInput(BaseModel):
    device: str


class InvalidFormInput(BaseModel):
    """Declares a form-only default its field rejects, with a multi-line Pydantic error."""

    rjsf_ui_schema: ClassVar[dict[str, Any]] = {"device": {"ui:widget": "radio"}}

    device: str
    count: Annotated[int, FormSchema(default="many")] = 1


class WireInvalidFormInput(BaseModel):
    """Passes semantic checks but violates the canonical envelope schema."""

    rjsf_ui_schema: ClassVar[dict[str, Any]] = {"device": {"ui:widget": None}}

    device: str


class OpaqueValue:
    """A valid Pydantic value with no JSON Schema representation."""


class InvalidJsonSchemaInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: OpaqueValue


class ExplodingJsonSchemaInput(BaseModel):
    value: str

    @classmethod
    def __get_pydantic_json_schema__(
        _cls,
        _core_schema: CoreSchema,
        _handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        raise RuntimeError("schema hook failed\nwith private details")


class ProviderInput(BaseModel):
    rjsf_ui_schema: ClassVar[dict[str, Any]] = {
        "profile": api_options(
            FormOptionSource(
                "fabric-profiles",
                params={"kind": "production"},
                depends_on={"site": Dependency("site")},
                clear_on_change=True,
            )
        )
    }

    site: str
    profile: str


FABRIC_PROFILES = FormOptionProvider(
    resolver="example_plugin.form_options:list_profiles",
    query_model="example_plugin.form_options:ProfileQuery",
)


class ExplodingProviderMapping(Mapping[str, FormOptionProvider]):
    def __getitem__(self, _key: str) -> FormOptionProvider:
        raise RuntimeError("mapping lookup failed\nwith private details")

    def __iter__(self) -> Iterator[str]:
        return iter(("fabric-profiles",))

    def __len__(self) -> int:
        return 1


@activity.defn
async def collect_facts() -> None: ...


@activity.defn
async def push_config() -> None: ...


class BackupScheduler:
    scheduler_identity = "alpha-plugin.backup"

    async def run(self) -> None: ...


class InventoryScheduler:
    scheduler_identity = "zulu-plugin.inventory"

    async def run(self) -> None: ...


class ComplianceScheduler:
    scheduler_identity = "zulu-plugin.compliance"

    async def run(self) -> None: ...


@workflow.defn
class AlphaWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Alpha"
    workflow_description = "Complete metadata, exposed over the API and MCP"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/alpha"
    workflow_form_enabled = True
    workflow_form_id = "alpha"
    workflow_mcp_enabled = True

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "alpha"


@workflow.defn
class BetaWorkflow(WorkflowMetadataMixin, StageMixin):
    workflow_name = "Beta"
    workflow_description = "Complete metadata, but not offered as an MCP tool"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/config/beta"
    workflow_form_enabled = True
    workflow_form_id = "beta"

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...

    @classmethod
    def get_workflow_cli_name(cls) -> str:
        return "beta"


@workflow.defn
class InternalWorkflow(WorkflowMetadataMixin, StageMixin):
    """Declares no metadata: the worker runs it, nothing else offers it."""

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn
class ApiDisabledWorkflow(WorkflowMetadataMixin, StageMixin):
    """Complete API metadata, but intentionally unavailable for direct invocation."""

    workflow_name = "API Disabled"
    workflow_description = "Invoked only by another workflow"
    workflow_input_class = DeviceInput
    workflow_api_endpoint = "/internal/api-disabled"

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


@workflow.defn
class LegacyApiWorkflow(WorkflowMetadataMixin, StageMixin):
    """Represent an existing API plugin written before browser forms existed."""

    workflow_name = "Legacy API"
    workflow_description = "Exposed through the API without browser-form metadata"
    workflow_input_class = DeviceInput
    workflow_api_enabled = True
    workflow_api_endpoint = "/legacy/run"

    @workflow.run
    async def run(self, workflow_input: BaseModel) -> None: ...


def plugin(
    name: str,
    *,
    version: str | None = None,
    workflows: tuple[type, ...] = (),
    activities: tuple[Any, ...] = (),
    schedulers: tuple[type, ...] = (),
) -> WorkflowPluginDescriptor:
    return WorkflowPluginDescriptor(
        name=name,
        version=version,
        workflows=workflows,
        activities=activities,
        schedulers=schedulers,
    )


def installed(*descriptors: WorkflowPluginDescriptor) -> dict[str, WorkflowPluginDescriptor]:
    return {descriptor.name: descriptor for descriptor in descriptors}


class TestEmptyRegistry:
    def test_a_registry_starts_out_empty(self) -> None:
        registry = WorkflowRegistry()

        assert registry.all_workflows == []
        assert registry.all_activities == []
        assert registry.all_schedulers == []
        assert registry.scheduler_registrations == ()
        assert registry.api_workflows == []
        assert registry.mcp_workflows == []
        assert registry.plugin_diagnostics == []

    def test_a_clean_install_with_no_plugins_builds_an_empty_registry(self) -> None:
        registry = WorkflowRegistry.build({})

        assert registry.all_workflows == []
        assert registry.plugin_diagnostics == []

    def test_a_plugin_contributing_nothing_still_reports_itself(self) -> None:
        registry = WorkflowRegistry.build(installed(plugin("empty-plugin", version="1.0.0")))

        assert registry.all_workflows == []
        assert registry.plugin_diagnostics == [
            PluginInfo(
                name="empty-plugin",
                version="1.0.0",
                workflow_count=0,
                activity_count=0,
                scheduler_count=0,
            )
        ]


class TestMergedCatalogs:
    def test_catalogs_are_ordered_by_plugin_name_then_by_declaration(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "zulu-plugin",
                    workflows=(InternalWorkflow,),
                    activities=(push_config,),
                    schedulers=(InventoryScheduler,),
                ),
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow, BetaWorkflow),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler,),
                ),
            )
        )

        assert registry.all_workflows == [AlphaWorkflow, BetaWorkflow, InternalWorkflow]
        assert registry.all_activities == [collect_facts, push_config]
        assert registry.all_schedulers == [BackupScheduler, InventoryScheduler]
        assert registry.scheduler_registrations == (
            SchedulerRegistration(
                plugin="alpha-plugin",
                identity="alpha-plugin.backup",
                scheduler=BackupScheduler,
            ),
            SchedulerRegistration(
                plugin="zulu-plugin",
                identity="zulu-plugin.inventory",
                scheduler=InventoryScheduler,
            ),
        )

    def test_the_builtin_plugin_comes_before_plugins_named_ahead_of_it(self) -> None:
        """API routes follow this order, so a plugin route must not shadow a built-in one."""
        registry = WorkflowRegistry.build(
            installed(
                plugin("acme", workflows=(AlphaWorkflow,)),
                plugin("builtin", workflows=(BetaWorkflow,)),
            )
        )

        assert registry.all_workflows == [BetaWorkflow, AlphaWorkflow]
        assert [info.name for info in registry.plugin_diagnostics] == ["builtin", "acme"]

    def test_scheduler_registration_provenance_is_immutable(self) -> None:
        registry = WorkflowRegistry.build(
            installed(plugin("alpha-plugin", schedulers=(BackupScheduler,)))
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            registry.scheduler_registrations[0].plugin = "renamed"  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_workflows_and_activities_contributed_by_two_plugins_are_registered_once(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow,),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler,),
                ),
                plugin(
                    "downstream-plugin",
                    workflows=(AlphaWorkflow,),
                    activities=(collect_facts,),
                ),
            )
        )

        assert registry.all_workflows == [AlphaWorkflow]
        assert registry.all_activities == [collect_facts]
        assert registry.all_schedulers == [BackupScheduler]

    def test_the_api_offers_only_workflows_that_opted_in(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(
                        AlphaWorkflow,
                        BetaWorkflow,
                        ApiDisabledWorkflow,
                        InternalWorkflow,
                    ),
                )
            )
        )

        assert registry.api_workflows == [AlphaWorkflow, BetaWorkflow]
        assert ApiDisabledWorkflow in registry.all_workflows

    def test_mcp_offers_only_workflows_that_opted_in(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow, BetaWorkflow, InternalWorkflow))
            )
        )

        assert registry.mcp_workflows == [AlphaWorkflow]


class TestPluginDiagnostics:
    def test_every_plugin_is_reported_in_name_order(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("zulu-plugin", version="2.0.0"), plugin("alpha-plugin", version="1.0.0")
            )
        )

        assert [info.name for info in registry.plugin_diagnostics] == [
            "alpha-plugin",
            "zulu-plugin",
        ]

    def test_a_plugin_of_unreported_version_is_named_as_such(self) -> None:
        """Discovery normally fills the version in; nothing hides it if it did not."""
        registry = WorkflowRegistry.build(installed(plugin("alpha-plugin")))

        assert registry.plugin_diagnostics[0].version == UNKNOWN_PLUGIN_VERSION

    def test_counts_describe_what_each_plugin_declared(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin(
                    "alpha-plugin",
                    workflows=(AlphaWorkflow,),
                    activities=(collect_facts,),
                    schedulers=(BackupScheduler,),
                ),
                plugin(
                    "zulu-plugin",
                    workflows=(AlphaWorkflow, InternalWorkflow),
                    activities=(collect_facts,),
                    schedulers=(InventoryScheduler, ComplianceScheduler),
                ),
            )
        )

        assert [
            (info.workflow_count, info.activity_count, info.scheduler_count)
            for info in registry.plugin_diagnostics
        ] == [
            (1, 1, 1),
            (2, 1, 2),
        ]

    def test_diagnostics_cannot_be_edited_after_the_registry_is_built(self) -> None:
        info = PluginInfo(
            name="alpha-plugin",
            version="1.0.0",
            workflow_count=1,
            activity_count=1,
            scheduler_count=0,
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(info, "version", "2.0.0")


class TestBuildInputs:
    def test_building_without_a_mapping_discovers_the_installed_plugins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registry_module,
            "discover_workflow_plugins",
            lambda: installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,))),
        )

        assert WorkflowRegistry.build().all_workflows == [AlphaWorkflow]

    def test_any_mapping_may_be_passed(self) -> None:
        """The registry copies what it is given rather than reordering it in place."""
        declared = installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,)))

        registry = WorkflowRegistry.build(MappingProxyType(declared))

        assert registry.all_workflows == [AlphaWorkflow]

    def test_a_rejected_plugin_set_fails_the_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_api_endpoint", "/config/alpha")

        with pytest.raises(WorkflowConflictError):
            WorkflowRegistry.build(
                installed(
                    plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                    plugin("beta-plugin", workflows=(BetaWorkflow,)),
                )
            )


class TestForms:
    def test_a_legacy_api_plugin_does_not_implicitly_opt_in_to_forms(self) -> None:
        plugins = installed(plugin("legacy-plugin", workflows=(LegacyApiWorkflow,)))
        registry = WorkflowRegistry.build(plugins)

        catalog = WorkflowFormCatalog.build(registry)
        report = validate_plugin_forms("legacy-plugin", plugins=plugins)

        assert registry.api_workflows == [LegacyApiWorkflow]
        assert catalog.forms == {}
        assert catalog.diagnostics == {}
        assert report.workflow_count == 1
        assert report.form_count == 0
        assert report.provider_count == 0

    def test_form_validation_rejects_a_form_without_an_api_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_api_enabled", False)
        monkeypatch.setattr(AlphaWorkflow, "workflow_mcp_enabled", False)
        plugins = installed(plugin("alpha-plugin", workflows=(AlphaWorkflow,)))

        with pytest.raises(
            WorkflowRegistrationError,
            match="enables a form but does not enable API",
        ):
            validate_plugin_forms("alpha-plugin", plugins=plugins)

    def test_every_api_workflow_gets_a_form_and_an_owner(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow, InternalWorkflow)),
                plugin("beta-plugin", workflows=(BetaWorkflow,)),
            )
        )
        catalog = WorkflowFormCatalog.build(registry)

        assert set(catalog.forms) == {AlphaWorkflow, BetaWorkflow}
        assert catalog.forms[AlphaWorkflow]["schema"]["required"] == ["device"]
        assert catalog.diagnostics == {}
        assert registry.owner(BetaWorkflow) == "beta-plugin"
        assert registry.owner(InternalWorkflow) == "alpha-plugin"

    def test_a_missing_third_party_workflow_form_id_is_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_form_id", None)
        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert BetaWorkflow not in catalog.form_ids
        assert "must be an explicit non-empty string" in catalog.diagnostics[BetaWorkflow].message

    def test_an_unexpected_third_party_form_id_accessor_error_is_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_form_id_error(_cls: type[BetaWorkflow]) -> str:
            raise RuntimeError("form ID accessor failed\nwith private details")

        monkeypatch.setattr(
            BetaWorkflow,
            "get_workflow_form_id",
            classmethod(raise_form_id_error),
        )
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                plugin("beta-plugin", workflows=(BetaWorkflow,)),
            )
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert AlphaWorkflow in catalog.forms
        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert BetaWorkflow not in catalog.form_ids
        assert catalog.diagnostics[BetaWorkflow].message == (
            "RuntimeError: form ID accessor failed with private details"
        )

    def test_an_unexpected_builtin_form_id_accessor_error_is_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_form_id_error(_cls: type[BetaWorkflow]) -> str:
            raise RuntimeError("form ID accessor failed")

        monkeypatch.setattr(
            BetaWorkflow,
            "get_workflow_form_id",
            classmethod(raise_form_id_error),
        )
        registry = WorkflowRegistry.build(
            installed(plugin(BUILTIN_PLUGIN_NAME, workflows=(BetaWorkflow,)))
        )

        with pytest.raises(RuntimeError, match="form ID accessor failed"):
            WorkflowFormCatalog.build(registry)

    @pytest.mark.parametrize("form_id", ["Beta", "beta_id"])
    def test_an_invalid_builtin_workflow_form_id_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        form_id: str,
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_form_id", form_id)
        registry = WorkflowRegistry.build(
            installed(plugin(BUILTIN_PLUGIN_NAME, workflows=(BetaWorkflow,)))
        )

        with pytest.raises(WorkflowFormContractError, match="workflow_form_id.*must match"):
            WorkflowFormCatalog.build(registry)

    def test_a_builtin_form_id_wins_over_a_third_party_collision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_form_id", "alpha")
        registry = WorkflowRegistry.build(
            installed(
                plugin(BUILTIN_PLUGIN_NAME, workflows=(AlphaWorkflow,)),
                plugin("beta-plugin", workflows=(BetaWorkflow,)),
            )
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert catalog.form_ids == {AlphaWorkflow: "alpha"}
        assert AlphaWorkflow in catalog.forms
        assert BetaWorkflow not in catalog.forms
        assert "conflicts with another workflow" in catalog.diagnostics[BetaWorkflow].message

    def test_two_third_party_form_id_collisions_are_both_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AlphaWorkflow, "workflow_form_id", "shared")
        monkeypatch.setattr(BetaWorkflow, "workflow_form_id", "shared")
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                plugin("beta-plugin", workflows=(BetaWorkflow,)),
            )
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert catalog.form_ids == {}
        assert catalog.forms == {}
        assert set(catalog.diagnostics) == {AlphaWorkflow, BetaWorkflow}

    def test_two_builtin_form_id_collisions_are_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_form_id", "alpha")
        registry = WorkflowRegistry.build(
            installed(
                plugin(BUILTIN_PLUGIN_NAME, workflows=(AlphaWorkflow, BetaWorkflow)),
            )
        )

        with pytest.raises(WorkflowFormContractError, match="multiple built-in workflows"):
            WorkflowFormCatalog.build(registry)

    def test_a_re_exported_workflow_is_owned_by_its_first_contributor(self) -> None:
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                plugin(BUILTIN_PLUGIN_NAME, workflows=(AlphaWorkflow,)),
            )
        )

        assert registry.owner(AlphaWorkflow) == BUILTIN_PLUGIN_NAME

    def test_an_invalid_third_party_form_is_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", InvalidFormInput)

        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                plugin("beta-plugin", workflows=(BetaWorkflow,)),
            )
        )
        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert catalog.form_ids[BetaWorkflow] == "beta"
        assert AlphaWorkflow in catalog.forms
        diagnostic = catalog.diagnostics[BetaWorkflow]
        assert diagnostic == WorkflowFormDiagnostic(
            plugin="beta-plugin", workflow="BetaWorkflow", message=diagnostic.message
        )
        assert "FormSchema default 'many' is not valid for the field" in diagnostic.message
        assert "\n" not in diagnostic.message

    def test_a_wire_invalid_third_party_form_is_isolated_and_fails_offline_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", WireInvalidFormInput)
        plugins = installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        registry = WorkflowRegistry.build(plugins)

        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert "form envelope $.ui_schema.device.ui:widget" in (
            catalog.diagnostics[BetaWorkflow].message
        )
        with pytest.raises(
            WorkflowFormContractError,
            match=r"workflow plugin 'beta-plugin' has unavailable forms:.*ui_schema.device.ui:widget",
        ):
            validate_plugin_forms("beta-plugin", plugins=plugins)

    @pytest.mark.parametrize(
        "ui_schema", [{"device": {"ui:widget": ["hidden"]}}, {"ui:order": [["device"]]}]
    )
    def test_a_wrongly_typed_third_party_declaration_is_isolated(
        self, monkeypatch: pytest.MonkeyPatch, ui_schema: dict[str, Any]
    ) -> None:
        class WronglyTypedInput(DeviceInput):
            rjsf_ui_schema: ClassVar[dict[str, Any]] = ui_schema

        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", WronglyTypedInput)

        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )
        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert "invalid form declaration (TypeError: unhashable type: 'list')" in (
            catalog.diagnostics[BetaWorkflow].message
        )

    def test_a_third_party_pydantic_schema_error_is_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", InvalidJsonSchemaInput)

        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )
        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert "invalid form declaration (PydanticInvalidForJsonSchema:" in (
            catalog.diagnostics[BetaWorkflow].message
        )

    def test_an_unexpected_third_party_schema_hook_error_is_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", ExplodingJsonSchemaInput)
        registry = WorkflowRegistry.build(
            installed(
                plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                plugin("beta-plugin", workflows=(BetaWorkflow,)),
            )
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert AlphaWorkflow in catalog.forms
        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert catalog.form_ids[BetaWorkflow] == "beta"
        assert catalog.diagnostics[BetaWorkflow].message == (
            "RuntimeError: schema hook failed with private details"
        )

    def test_an_unexpected_builtin_schema_hook_error_is_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", ExplodingJsonSchemaInput)
        registry = WorkflowRegistry.build(
            installed(plugin(BUILTIN_PLUGIN_NAME, workflows=(BetaWorkflow,)))
        )

        with pytest.raises(RuntimeError, match="schema hook failed"):
            WorkflowFormCatalog.build(registry)

    def test_an_invalid_builtin_form_fails_the_form_catalog_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", InvalidFormInput)
        registry = WorkflowRegistry.build(
            installed(plugin(BUILTIN_PLUGIN_NAME, workflows=(BetaWorkflow,)))
        )

        with pytest.raises(WorkflowFormContractError, match="FormSchema default"):
            WorkflowFormCatalog.build(registry)

    def test_a_wire_invalid_builtin_form_fails_the_form_catalog_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", WireInvalidFormInput)
        registry = WorkflowRegistry.build(
            installed(plugin(BUILTIN_PLUGIN_NAME, workflows=(BetaWorkflow,)))
        )

        with pytest.raises(
            WorkflowFormContractError, match=r"form envelope \$.ui_schema.device.ui:widget"
        ):
            WorkflowFormCatalog.build(registry)

    def test_a_builtin_pydantic_schema_error_fails_the_form_catalog_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", InvalidJsonSchemaInput)
        registry = WorkflowRegistry.build(
            installed(plugin(BUILTIN_PLUGIN_NAME, workflows=(BetaWorkflow,)))
        )

        with pytest.raises(WorkflowFormContractError, match="PydanticInvalidForJsonSchema"):
            WorkflowFormCatalog.build(registry)

    def test_a_referenced_provider_is_compiled_into_the_existing_wire_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = ProviderInput.rjsf_ui_schema["profile"]["ui:options"]["source"]
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", ProviderInput)
        monkeypatch.setattr(
            BetaWorkflow,
            "workflow_form_option_providers",
            {"fabric-profiles": FABRIC_PROFILES, "unused": object()},
        )
        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert catalog.forms[BetaWorkflow]["ui_schema"]["profile"]["ui:options"]["source"] == {
            "endpoint": "/v1/workflow/beta/form-options/fabric-profiles",
            "label_key": "label",
            "value_key": "value",
            "params": {"kind": "production"},
            "depends_on": {"site": {"field": "site"}},
            "clear_on_change": True,
            "response": "options-v1",
        }
        assert catalog.providers == (
            FormOptionProviderBinding(
                workflow=BetaWorkflow,
                workflow_form_id="beta",
                plugin="beta-plugin",
                source="fabric-profiles",
                endpoint="/v1/workflow/beta/form-options/fabric-profiles",
                declaration=FABRIC_PROFILES,
                uses=(source,),
            ),
        )
        assert catalog.warnings == (
            WorkflowFormWarning(
                plugin="beta-plugin",
                workflow="BetaWorkflow",
                message="form option provider 'unused' is declared but not referenced",
            ),
        )

    def test_a_missing_third_party_provider_is_isolated_from_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", ProviderInput)
        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert catalog.providers == ()
        assert "is referenced by the form but is not declared" in (
            catalog.diagnostics[BetaWorkflow].message
        )

    def test_an_unexpected_third_party_provider_mapping_error_is_isolated_atomically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", ProviderInput)
        monkeypatch.setattr(
            BetaWorkflow,
            "workflow_form_option_providers",
            ExplodingProviderMapping(),
        )
        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert BetaWorkflow in registry.api_workflows
        assert BetaWorkflow not in catalog.forms
        assert catalog.providers == ()
        assert catalog.warnings == ()
        assert catalog.diagnostics[BetaWorkflow].message == (
            "RuntimeError: mapping lookup failed with private details"
        )

    def test_a_workflow_form_needs_a_lowercase_kebab_case_form_id(
        self,
    ) -> None:
        unicode_workflow = type(
            "BétaWorkflow",
            (WorkflowMetadataMixin,),
            {
                "workflow_input_class": ProviderInput,
                "workflow_form_enabled": True,
                "workflow_form_id": "Béta",
                "workflow_form_option_providers": {"fabric-profiles": FABRIC_PROFILES},
            },
        )
        registry = WorkflowRegistry(
            api_workflows=[unicode_workflow],
            workflow_owners={unicode_workflow: "beta-plugin"},
        )

        catalog = WorkflowFormCatalog.build(registry)

        assert unicode_workflow not in catalog.forms
        assert "must match '^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$'" in (
            catalog.diagnostics[unicode_workflow].message
        )

    def test_execution_registration_ignores_provider_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            BetaWorkflow,
            "workflow_form_option_providers",
            {"broken": object()},
        )

        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )

        assert BetaWorkflow in registry.api_workflows

    def test_api_compilation_can_atomically_withdraw_a_third_party_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", ProviderInput)
        monkeypatch.setattr(
            BetaWorkflow,
            "workflow_form_option_providers",
            {"fabric-profiles": FABRIC_PROFILES},
        )
        registry = WorkflowRegistry.build(
            installed(plugin("beta-plugin", workflows=(BetaWorkflow,)))
        )
        catalog = WorkflowFormCatalog.build(registry)

        catalog.unavailable(BetaWorkflow, "provider\nfailed")

        assert BetaWorkflow not in catalog.forms
        assert catalog.form_ids[BetaWorkflow] == "beta"
        assert catalog.providers == ()
        assert catalog.diagnostics[BetaWorkflow] == WorkflowFormDiagnostic(
            plugin="beta-plugin",
            workflow="BetaWorkflow",
            message="provider failed",
        )

    def test_ordinary_registration_errors_stay_fatal_for_third_parties(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(BetaWorkflow, "workflow_input_class", InvalidFormInput)
        monkeypatch.setattr(BetaWorkflow, "workflow_api_endpoint", "/config/alpha")

        with pytest.raises(WorkflowConflictError):
            WorkflowRegistry.build(
                installed(
                    plugin("alpha-plugin", workflows=(AlphaWorkflow,)),
                    plugin("beta-plugin", workflows=(BetaWorkflow,)),
                )
            )
