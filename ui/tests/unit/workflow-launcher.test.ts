/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import legacyWorkflowRedirects from "@/config/legacy-workflow-redirects.json";
import workflowFormIds from "@/config/workflow-form-ids.json";
import { siteConfig } from "@/config/site";
import { workflowFormPath } from "@/config/workflow-redirects.mjs";
import {
  DEFAULT_WORKFLOW_GROUP,
  normalizeWorkflowCatalog,
  normalizeWorkflowCatalogEntry,
} from "@/lib/workflow-catalog";
import {
  buildWorkflowLauncherItems,
  getWorkflowExecutePermission,
  groupWorkflowLauncherItems,
  WORKFLOW_FORM_API_UPGRADE_REQUIRED,
  type WorkflowLauncherItem,
  type WorkflowLauncherOverrides,
} from "@/lib/workflow-launcher";
import { workflowMetadata as mswWorkflowMetadata } from "@/mocks/handlers/workflowHandlers";
import type {
  WorkflowCatalogEntry,
  WorkflowCatalogEntryWire,
  WorkflowCatalogResponseWire,
} from "@/types/workflow-catalog.types";

/**
 * `siteConfig.workflows` as it stood before the launcher read the catalog: the
 * launcher listed exactly these, in this order, linking to `/workflows/<slug>/form`.
 */
const LEGACY_SITE_WORKFLOWS = [
  {
    title: "Configuration Backup",
    workflowName: "BackupWorkflow",
    slug: "backupworkflow",
  },
  {
    title: "Site Configuration Backup",
    workflowName: "SiteBackupWorkflow",
    slug: "sitebackupworkflow",
  },
  {
    title: "Connected Host Metadata",
    workflowName: "ConnectedHostMetadataWorkflow",
    slug: "connectedhostmetadataworkflow",
  },
  {
    title: "Configuration Deploy",
    workflowName: "DeployWorkflow",
    slug: "deployworkflow",
  },
  {
    title: "Configuration Diff",
    workflowName: "ConfigDiffWorkflow",
    slug: "configdiffworkflow",
  },
  {
    title: "Multi-Configuration Deploy",
    workflowName: "MultiDeployWorkflow",
    slug: "multideployworkflow",
  },
  {
    title: "Device Cable Validation",
    workflowName: "DeviceCableValidationWorkflow",
    slug: "devicecablevalidationworkflow",
  },
  {
    title: "Site Cable Validation",
    workflowName: "SiteCableValidationWorkflow",
    slug: "sitecablevalidationworkflow",
  },
  {
    title: "Port LLDP Info",
    workflowName: "PortLLDPInfoWorkflow",
    slug: "portlldpinfoworkflow",
  },
  {
    title: "SpX Overlay Creation",
    workflowName: "SpXOverlayCreationWorkflow",
    slug: "spxoverlaycreationworkflow",
  },
  {
    title: "SpX Overlay Deletion",
    workflowName: "SpXOverlayDeletionWorkflow",
    slug: "spxoverlaydeletionworkflow",
  },
  {
    title: "SpX Overlay Tenant Change",
    workflowName: "SpXOverlayTenantChangeWorkflow",
    slug: "spxoverlaytenantchangeworkflow",
  },
  {
    title: "InfiniBand Get Unhealthy Ports",
    workflowName: "InfinibandGetUnhealthyPortsWorkflow",
    slug: "infinibandgetunhealthyportsworkflow",
  },
  {
    title: "InfiniBand Cable Validation",
    workflowName: "InfinibandCableValidationWorkflow",
    slug: "infinibandcablevalidationworkflow",
  },
  {
    title: "InfiniBand MLNX-OS Upgrade",
    workflowName: "InfinibandMlnxOSUpgradeWorkflow",
    slug: "infinibandmlnxosupgradeworkflow",
  },
  {
    title: "Reprovision",
    workflowName: "ReprovisionWorkflow",
    slug: "reprovisionworkflow",
  },
  {
    title: "Switch OS Upgrade",
    workflowName: "SwitchOSUpgradeWorkflow",
    slug: "switchosupgradeworkflow",
  },
  {
    title: "Cumulus Hardware Validation",
    workflowName: "ValidateHardwareWorkflow",
    slug: "cumulushardwarevalidationworkflow",
  },
  {
    title: "Device Password Rotation",
    workflowName: "DevicePasswordRotationWorkflow",
    slug: "devicepasswordrotationworkflow",
  },
  {
    title: "Site Password Rotation",
    workflowName: "SitePasswordRotationWorkflow",
    slug: "sitepasswordrotationworkflow",
  },
  {
    title: "Device Diagnostics",
    workflowName: "DiagnosticsWorkflow",
    slug: "diagnosticsworkflow",
  },
  {
    title: "InfiniBand Port GUID Discovery",
    workflowName: "IBPortGuidDiscoveryWorkflow",
    slug: "ibportguiddiscoveryworkflow",
  },
  {
    title: "InfiniBand PKey Creation",
    workflowName: "IBPKeyCreationWorkflow",
    slug: "ibpkeycreationworkflow",
  },
  {
    title: "InfiniBand PKey Member Add",
    workflowName: "IBPKeyMemberAddWorkflow",
    slug: "ibpkeymemberaddworkflow",
  },
  {
    title: "InfiniBand PKey Member Update",
    workflowName: "IBPKeyMemberUpdateWorkflow",
    slug: "ibpkeymemberupdateworkflow",
  },
  {
    title: "InfiniBand PKey Member Delete",
    workflowName: "IBPKeyMemberDeleteWorkflow",
    slug: "ibpkeymemberdeleteworkflow",
  },
];

const LEGACY_LAUNCHER = LEGACY_SITE_WORKFLOWS.map(
  ({ title, workflowName }) => ({
    name: workflowName,
    display_name: title,
    href: workflowFormPath(
      workflowFormIds[workflowName as keyof typeof workflowFormIds]
    ),
  })
);

const launcherCollator = new Intl.Collator("en", { numeric: true });
const ALPHABETICAL_LAUNCHER = [...LEGACY_LAUNCHER].sort(
  (a, b) =>
    launcherCollator.compare(a.display_name, b.display_name) ||
    launcherCollator.compare(a.name, b.name)
);

/**
 * Add the new API's explicit form availability to the additive compatibility baseline.
 * The baseline intentionally omits newly added defaults, so it is not a full current
 * response; its explicit `false` values are retained by property order.
 */
const SERVER_METADATA_BASELINE = fileURLToPath(
  new URL(
    "../../../src/tests/temporal/api/fixtures/workflow_metadata_baseline.json",
    import.meta.url
  )
);

const serverCatalog = (): WorkflowCatalogEntry[] =>
  normalizeWorkflowCatalog({
    workflows: (
      JSON.parse(
        readFileSync(SERVER_METADATA_BASELINE, "utf8")
      ) as WorkflowCatalogResponseWire
    ).workflows.map((workflow) => ({
      has_form: true,
      form_id:
        workflowFormIds[workflow.name as keyof typeof workflowFormIds] ?? null,
      ...workflow,
    })),
  });

const overrides = siteConfig.workflowOverrides;

const visible = (items: WorkflowLauncherItem[]) =>
  items.map(({ name, display_name, href }) => ({ name, display_name, href }));

const catalogEntry = (
  name: string,
  display_name: string,
  extra: Partial<WorkflowCatalogEntryWire> = {}
): WorkflowCatalogEntry =>
  normalizeWorkflowCatalogEntry({
    name,
    display_name,
    description: `${display_name} workflow`,
    endpoint: `/${name.toLowerCase()}`,
    namespace: null,
    cli_name: name.toLowerCase(),
    input_class: `${name}Input`,
    read_roles: ["all"],
    execute_roles: ["all"],
    has_form: true,
    form_id:
      workflowFormIds[name as keyof typeof workflowFormIds] ??
      name
        .replace(/Workflow$/, "")
        .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
        .toLowerCase(),
    ...extra,
  });

const PLUGIN = catalogEntry("AcmeFabricAuditWorkflow", "Acme Fabric Audit", {
  form_id: "acme-fabric-audit",
});

describe("buildWorkflowLauncherItems with the shipped overrides", () => {
  it("lists the real server catalog alphabetically", () => {
    const catalog = serverCatalog();
    expect(catalog.length).toBeGreaterThan(LEGACY_LAUNCHER.length);

    const items = buildWorkflowLauncherItems(catalog, overrides);

    expect(visible(items)).toEqual(ALPHABETICAL_LAUNCHER);
    expect(items.every((item) => item.metadata !== undefined)).toBe(true);
  });

  it("does not depend on server order", () => {
    const items = buildWorkflowLauncherItems(
      [...serverCatalog()].reverse(),
      overrides
    );

    expect(visible(items)).toEqual(ALPHABETICAL_LAUNCHER);
  });

  it("lists the MSW mock catalog alphabetically", () => {
    const items = buildWorkflowLauncherItems(
      normalizeWorkflowCatalog(mswWorkflowMetadata),
      overrides
    );

    expect(visible(items)).toEqual(
      ALPHABETICAL_LAUNCHER.map((item) =>
        item.name === "ConfigDiffWorkflow" ? { ...item, href: "" } : item
      )
    );
  });

  it("lists the built-ins without metadata while the catalog is unavailable", () => {
    const items = buildWorkflowLauncherItems([], overrides);

    expect(visible(items)).toEqual(
      ALPHABETICAL_LAUNCHER.map((item) => ({ ...item, href: "" }))
    );
    expect(items.every((item) => item.metadata === undefined)).toBe(true);
  });

  it("keeps a built-in the catalog lacks alphabetized, without metadata", () => {
    const catalog = serverCatalog().filter(
      (entry) => entry.name !== "ConfigDiffWorkflow"
    );

    const items = buildWorkflowLauncherItems(catalog, overrides);

    expect(visible(items)).toEqual(
      ALPHABETICAL_LAUNCHER.map((item) =>
        item.name === "ConfigDiffWorkflow" ? { ...item, href: "" } : item
      )
    );
    expect(
      items.find((item) => item.name === "ConfigDiffWorkflow")?.metadata
    ).toBeUndefined();
  });

  it("alphabetizes an unmapped plugin workflow on its form ID route", () => {
    const items = buildWorkflowLauncherItems(
      [...serverCatalog(), PLUGIN],
      overrides
    );

    expect(visible(items)).toEqual([
      {
        name: "AcmeFabricAuditWorkflow",
        display_name: "Acme Fabric Audit",
        href: "/workflows/new/acme-fabric-audit",
      },
      ...ALPHABETICAL_LAUNCHER,
    ]);
    expect(items[0]).toMatchObject({
      group: DEFAULT_WORKFLOW_GROUP,
      metadata: PLUGIN,
    });
  });

  it("covers each legacy form with exactly one listed override", () => {
    const listed = Object.entries(overrides)
      .filter(([, override]) => !override.hidden)
      .map(([name]) => name);

    expect([...listed].sort()).toEqual(
      Object.keys(legacyWorkflowRedirects).sort()
    );
  });
});

describe("buildWorkflowLauncherItems rules", () => {
  const builtIn = catalogEntry("DeployWorkflow", "Configuration Deploy");

  it("lists a catalog workflow that has no override", () => {
    expect(visible(buildWorkflowLauncherItems([PLUGIN], {}))).toEqual([
      {
        name: "AcmeFabricAuditWorkflow",
        display_name: "Acme Fabric Audit",
        href: "/workflows/new/acme-fabric-audit",
      },
    ]);
  });

  it("drops workflows an override hides", () => {
    const items = buildWorkflowLauncherItems([builtIn, PLUGIN], {
      AcmeFabricAuditWorkflow: { hidden: true },
    });

    expect(items.map((item) => item.name)).toEqual(["DeployWorkflow"]);
  });

  it("drops form-less workflows", () => {
    const formLessBuiltIn = catalogEntry(
      "DeployWorkflow",
      "Configuration Deploy",
      {
        has_form: false,
      }
    );
    const formLessPlugin = catalogEntry("NoFormWorkflow", "No Form", {
      has_form: false,
    });

    expect(
      buildWorkflowLauncherItems([formLessBuiltIn, formLessPlugin], {})
    ).toEqual([]);
  });

  it("keeps workflows from an older API disabled instead of creating broken links", () => {
    const oldApiEntry = catalogEntry("DeployWorkflow", "Configuration Deploy", {
      has_form: null,
    });
    const [item] = buildWorkflowLauncherItems([oldApiEntry], {});

    expect(item.metadata).toBe(oldApiEntry);
    expect(
      getWorkflowExecutePermission(item.metadata, new Set(), false)
    ).toEqual({
      allowed: false,
      reason: WORKFLOW_FORM_API_UPGRADE_REQUIRED,
    });
  });

  it("fails closed when a form-enabled workflow omits its form ID", () => {
    const missingFormId = catalogEntry(
      "DeployWorkflow",
      "Configuration Deploy",
      { form_id: null }
    );
    const [item] = buildWorkflowLauncherItems([missingFormId], {});

    expect(item.href).toBe("");
    expect(
      getWorkflowExecutePermission(item.metadata, new Set(["all"]), false)
    ).toEqual({
      allowed: false,
      reason: WORKFLOW_FORM_API_UPGRADE_REQUIRED,
    });
  });

  it("prefers the catalog display name and falls back to the override title", () => {
    const renamed = catalogEntry("DeployWorkflow", "Deploy Configuration");
    const override: WorkflowLauncherOverrides = {
      DeployWorkflow: { title: "Configuration Deploy" },
    };

    expect(
      buildWorkflowLauncherItems([renamed], override)[0].display_name
    ).toBe("Deploy Configuration");
    expect(buildWorkflowLauncherItems([], override)[0].display_name).toBe(
      "Configuration Deploy"
    );
    expect(
      buildWorkflowLauncherItems([], { DeployWorkflow: {} })[0].display_name
    ).toBe("DeployWorkflow");
  });

  it("uses the catalog group", () => {
    const fromCatalog = catalogEntry("DeployWorkflow", "Configuration Deploy", {
      group: "Catalog",
    });

    expect(buildWorkflowLauncherItems([fromCatalog], {})[0]).toMatchObject({
      group: "Catalog",
    });
  });

  it("sorts by display name and class name", () => {
    const catalog = [
      catalogEntry("ZetaWorkflow", "Alpha"),
      catalogEntry("AlphaWorkflow", "Alpha"),
      catalogEntry("LateWorkflow", "Late"),
      catalogEntry("BetaWorkflow", "Beta"),
      catalogEntry("EarlyWorkflow", "Zulu"),
    ];

    expect(
      buildWorkflowLauncherItems(catalog, {}).map((item) => item.name)
    ).toEqual([
      "AlphaWorkflow",
      "ZetaWorkflow",
      "BetaWorkflow",
      "LateWorkflow",
      "EarlyWorkflow",
    ]);
  });

  it("links workflows to the form ID route", () => {
    expect(buildWorkflowLauncherItems([builtIn], {})[0].href).toBe(
      "/workflows/new/deploy"
    );
  });
});

describe("groupWorkflowLauncherItems", () => {
  it("keeps everything in one section when no group is declared", () => {
    const deploy = catalogEntry("DeployWorkflow", "Configuration Deploy");
    const sections = groupWorkflowLauncherItems(
      buildWorkflowLauncherItems([deploy, PLUGIN], {})
    );

    expect(sections).toHaveLength(1);
    expect(sections[0].group).toBe(DEFAULT_WORKFLOW_GROUP);
    expect(sections[0].items.map((item) => item.name)).toEqual([
      "AcmeFabricAuditWorkflow",
      "DeployWorkflow",
    ]);
  });

  it("uses the backend groups for the shipped workflows", () => {
    const sections = groupWorkflowLauncherItems(
      buildWorkflowLauncherItems(serverCatalog(), overrides)
    );

    expect(sections.map((section) => section.group)).toEqual([
      "Configuration",
      "InfiniBand",
      "Lifecycle & Security",
      "SpX Overlays",
      "Validation & Diagnostics",
    ]);
    for (const section of sections) {
      const names = section.items.map((item) => item.display_name);
      expect(names).toEqual([...names].sort(launcherCollator.compare));
    }
  });

  it("orders sections alphabetically and puts the default section last", () => {
    const items = buildWorkflowLauncherItems(
      [
        catalogEntry("AWorkflow", "A"),
        catalogEntry("BWorkflow", "B", { group: "Fabric" }),
        catalogEntry("CWorkflow", "C", { group: "Access" }),
        catalogEntry("DWorkflow", "D", { group: "Fabric" }),
      ],
      {}
    );

    expect(
      groupWorkflowLauncherItems(items).map(
        ({ group, items: sectionItems }) => [
          group,
          sectionItems.map((item) => item.name),
        ]
      )
    ).toEqual([
      ["Access", ["CWorkflow"]],
      ["Fabric", ["BWorkflow", "DWorkflow"]],
      [DEFAULT_WORKFLOW_GROUP, ["AWorkflow"]],
    ]);
  });
});

describe("getWorkflowExecutePermission", () => {
  const roles = (...names: string[]) => new Set(names);
  const deploy = catalogEntry("DeployWorkflow", "Configuration Deploy", {
    execute_roles: ["DeployWorkflow", "executor"],
  });

  it("allows a user holding one of the execute roles", () => {
    expect(
      getWorkflowExecutePermission(deploy, roles("reader", "executor"), false)
    ).toEqual({
      allowed: true,
    });
  });

  it('allows everyone when the execute roles include "all"', () => {
    expect(
      getWorkflowExecutePermission(catalogEntry("A", "A"), roles(), false)
    ).toEqual({
      allowed: true,
    });
  });

  it("names the required roles when the user has none of them", () => {
    expect(
      getWorkflowExecutePermission(deploy, roles("all", "reader"), false)
    ).toEqual({
      allowed: false,
      reason: "Required execute roles: DeployWorkflow, executor",
    });
  });

  it("explains unconfigured roles, missing metadata, and a failed /whoami", () => {
    expect(
      getWorkflowExecutePermission(
        catalogEntry("A", "A", { execute_roles: [] }),
        roles(),
        false
      )
    ).toEqual({
      allowed: false,
      reason: "Required execute roles are not configured.",
    });
    expect(
      getWorkflowExecutePermission(undefined, roles("executor"), false)
    ).toEqual({
      allowed: false,
      reason: "Workflow metadata is unavailable.",
    });
    expect(
      getWorkflowExecutePermission(catalogEntry("A", "A"), roles(), true)
    ).toEqual({
      allowed: false,
      reason: "Unauthorized",
    });
  });
});
