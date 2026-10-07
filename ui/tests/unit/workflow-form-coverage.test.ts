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
/**
 * Coverage policy (plan section 20): every workflow with a `/form` (the server snapshot,
 * which already leaves out `workflow_form_exclusions.json`) has a Playwright spec with a
 * render and a submission scenario on its class-name route. Adding a workflow to the
 * snapshot fails here until its spec is listed.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { SERVER_FORM_EXCLUSIONS, SERVER_WORKFLOW_FORMS } from "./server-snapshot";

/** Workflow class name -> the spec under `tests/e2e/` that renders and submits it. */
const RENDER_AND_SUBMIT_SPECS: Readonly<Record<string, string>> = {
  BackupWorkflow: "backupConfigForm.spec.ts",
  ConfigDiffWorkflow: "configDiffForm.spec.ts",
  ConnectedHostMetadataWorkflow: "connectedHostMetaDataForm.spec.ts",
  DeployWorkflow: "deployWorkflowForm.spec.ts",
  DeviceCableValidationWorkflow: "deviceCableValidationForm.spec.ts",
  DevicePasswordRotationWorkflow: "devicePasswordRotationForm.spec.ts",
  DiagnosticsWorkflow: "diagnosticsForm.spec.ts",
  IBPKeyCreationWorkflow: "ibPkeyCreationForm.spec.ts",
  IBPKeyMemberAddWorkflow: "ibPkeyMemberAddForm.spec.ts",
  IBPKeyMemberDeleteWorkflow: "ibPkeyMemberDeleteForm.spec.ts",
  IBPKeyMemberUpdateWorkflow: "ibPkeyMemberUpdateForm.spec.ts",
  IBPortGuidDiscoveryWorkflow: "ibPortGuidDiscoveryForm.spec.ts",
  InfinibandCableValidationWorkflow: "ibCableValidationForm.spec.ts",
  InfinibandGetUnhealthyPortsWorkflow: "ibGetUnhealthyPortsForm.spec.ts",
  InfinibandMlnxOSUpgradeWorkflow: "ibOsUpgradeForm.spec.ts",
  MultiDeployWorkflow: "multiDeployWorkflowForm.spec.ts",
  PortLLDPInfoWorkflow: "portlldpinfoForm.spec.ts",
  ReprovisionWorkflow: "reprovisionForm.spec.ts",
  SiteBackupWorkflow: "siteBackupForm.spec.ts",
  SiteCableValidationWorkflow: "siteCableValidationForm.spec.ts",
  SitePasswordRotationWorkflow: "sitePasswordRotationForm.spec.ts",
  SpXOverlayCreationWorkflow: "spxOverlayCreationForm.spec.ts",
  SpXOverlayDeletionWorkflow: "spxOverlayDeletionForm.spec.ts",
  SpXOverlayTenantChangeWorkflow: "spxOverlayTenantChangePorts.spec.ts",
  SwitchOSUpgradeWorkflow: "switchOsUpgradeForm.spec.ts",
  ValidateHardwareWorkflow: "cumulusHardwareValidationForm.spec.ts",
};

const specSource = (file: string) =>
  readFileSync(new URL(`../e2e/${file}`, import.meta.url), "utf8");

describe("workflow form coverage", () => {
  it("covers exactly the workflows with a /form", () => {
    expect(Object.keys(RENDER_AND_SUBMIT_SPECS).sort()).toEqual(
      Object.keys(SERVER_WORKFLOW_FORMS).sort()
    );
    for (const excluded of Object.keys(SERVER_FORM_EXCLUSIONS)) {
      expect(SERVER_WORKFLOW_FORMS).not.toHaveProperty(excluded);
    }
  });

  it.each(Object.entries(RENDER_AND_SUBMIT_SPECS))(
    "%s: %s opens its class-name route and checks a POST",
    (workflow, file) => {
      const source = specSource(file);
      // The route, or the shared suite's `workflow:` (it opens `/workflows/new/<name>`).
      expect(source).toMatch(
        new RegExp(`/workflows/new/${workflow}\\b|formPath\\("${workflow}"\\)|workflow: "${workflow}"`)
      );
      // A submission: a captured POST, or the shared device-form suite (which has one).
      expect(source).toMatch(/nextPost\(|waitForRequest\(|runWorkflowFormTests\(/);
    }
  );
});
