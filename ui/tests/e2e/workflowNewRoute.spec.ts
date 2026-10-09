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

import {
  expect,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

import legacyWorkflowRedirects from "@/config/legacy-workflow-redirects.json";
import workflowFormIds from "@/config/workflow-form-ids.json";
import {
  DEVICES_LIST,
  SITES_LIST,
  STATUS_LIST,
  TENANT_LIST,
} from "@/mocks/data";

import {
  SERVER_WORKFLOW_FORMS,
  SERVER_WORKFLOW_METADATA,
  mockServerCatalogAndUser,
  mockTypedLocationsEndpoint,
} from "./shared/apiMocks";
import { test, TEST_TIMEOUT, WORKFLOW_DETAILS_TIMEOUT } from "./shared/utils";

const DEPLOY_TITLE = "New Configuration Deploy Workflow";
const DEPLOY_ROLES_REASON = "Required execute roles: DeployWorkflow, executor";
const API_UPGRADE_REQUIRED =
  "Upgrade the Config Manager workflow API before deploying this UI version. Browser workflow forms require the backend form metadata and endpoints.";
// TenantB, Active: matches the Nautobot link's tenant and status filters too.
const DEVICE = DEVICES_LIST[SITES_LIST.pdx01][0];
const formIdOf = (name: keyof typeof workflowFormIds) => workflowFormIds[name];

const escapeRegExp = (text: string) =>
  text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** SelectBox trigger whose stable field label precedes this selection or placeholder. */
const picker = (page: Page, name: string) =>
  page.getByRole("combobox", { name: new RegExp(`: ${escapeRegExp(name)}$`) });

const choose = async (page: Page, trigger: string, option: string) => {
  await picker(page, trigger).click();
  await page
    .getByRole("dialog")
    .getByRole("option", { name: option, exact: true })
    .click();
};

const isDeviceOptionsRequest = (request: Request) =>
  new URL(request.url()).pathname === "/v1/parameter/device";

const isDeploySubmit = (request: Request) =>
  request.method() === "POST" &&
  new URL(request.url()).pathname === "/v1/workflow/ngc/deploy";

/** Query of an option request as sorted `[name, value]` pairs (repeats kept). */
const queryOf = (request: Request) =>
  [...new URL(request.url()).searchParams.entries()].sort(
    ([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y)
  );

const formRequests = (page: Page) => {
  const paths: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (/^\/v1\/workflow\/[^/]+\/form$/.test(pathname)) paths.push(pathname);
  });
  return paths;
};

test.describe("/workflows/new/<form_id>", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await mockTypedLocationsEndpoint(page);
  });

  test("DeployWorkflow: filters and device picker, devices only after a site, submits device_id and commit_confirm", async ({
    page,
  }) => {
    const deviceRequests: Request[] = [];
    page.on("request", (request) => {
      if (isDeviceOptionsRequest(request)) deviceRequests.push(request);
    });

    await page.goto("/workflows/new/deploy");

    await expect(
      page.getByRole("heading", { name: DEPLOY_TITLE })
    ).toBeVisible();
    // Required fields carry a visible " *" (hidden from their accessible names).
    for (const label of [
      "Site *",
      "Tenant (optional)",
      "Status (optional)",
      "Device *",
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(
      page.getByRole("checkbox", { name: "Use commit-confirm" })
    ).toBeChecked();
    await expect(picker(page, "Select a Site...")).toBeEnabled();
    await expect(picker(page, "Select Tenant (optional)...")).toBeEnabled();
    await expect(picker(page, "Select Status (optional)...")).toBeEnabled();

    await expect(picker(page, "Select a Site first")).toBeDisabled();
    expect(deviceRequests).toEqual([]);

    const devicesRequest = page.waitForRequest(isDeviceOptionsRequest);
    await choose(page, "Select a Site...", SITES_LIST.pdx01);
    expect(queryOf(await devicesRequest)).toEqual([
      ["managed_only", "true"],
      ["site", SITES_LIST.pdx01],
      ["site_type", "Site"],
    ]);

    await choose(page, "Select a Device...", DEVICE.name);
    await expect(picker(page, `${DEVICE.name}. Open options`)).toBeVisible();

    const submit = page.waitForRequest(isDeploySubmit);
    await page.getByRole("button", { name: "Submit" }).click();
    expect((await submit).postDataJSON()).toEqual({
      device_id: DEVICE.id,
      commit_confirm: true,
    });

    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({
      timeout: WORKFLOW_DETAILS_TIMEOUT,
    });
    expect(new URL(page.url()).pathname).toBe(`/workflows/${DEVICE.id}`);
  });

  test("DeployWorkflow: a Nautobot link pre-selects site, tenant, status, and device", async ({
    page,
  }) => {
    const deviceRequests: Request[] = [];
    page.on("request", (request) => {
      if (isDeviceOptionsRequest(request)) deviceRequests.push(request);
    });
    await page.goto(
      `/workflows/new/deploy?site=${SITES_LIST.pdx01}&device-id=${DEVICE.id}` +
        `&tenant=${TENANT_LIST.ngc}&status=${STATUS_LIST.active}`
    );

    await expect(picker(page, `${SITES_LIST.pdx01}. Open options`)).toBeVisible(
      {
        timeout: TEST_TIMEOUT,
      }
    );
    await expect(
      picker(page, `${TENANT_LIST.ngc}. Open options`)
    ).toBeVisible();
    await expect(
      picker(page, `${STATUS_LIST.active}. Open options`)
    ).toBeVisible();
    await expect(picker(page, `${DEVICE.name}. Open options`)).toBeVisible({
      timeout: TEST_TIMEOUT,
    });
    // The device is matched against the list filtered by every pre-selected filter. (The
    // list may load once with the site alone while tenant and status are still matched.)
    expect(queryOf(deviceRequests.at(-1)!)).toEqual([
      ["managed_only", "true"],
      ["site", SITES_LIST.pdx01],
      ["site_type", "Site"],
      ["status", STATUS_LIST.active],
      ["tenant", TENANT_LIST.ngc],
    ]);

    const submit = page.waitForRequest(isDeploySubmit);
    await page.getByRole("button", { name: "Submit" }).click();
    expect((await submit).postDataJSON()).toEqual({
      device_id: DEVICE.id,
      commit_confirm: true,
    });
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible({
      timeout: WORKFLOW_DETAILS_TIMEOUT,
    });
  });

  test("renders the other first-group RJSF forms from the server snapshot", async ({
    page,
  }) => {
    const forms = [
      {
        name: "IBPKeyCreationWorkflow",
        title: "New InfiniBand PKey Creation Workflow",
        labels: ["UFM Host *", "PKey (optional)"],
        submit: "Create PKey", // ui:submitButtonOptions.submitText
      },
      {
        name: "SiteCableValidationWorkflow",
        title: "New Site Cable Validation Workflow",
        labels: ["Site *", "Roles", "Device Status", "Tenant"],
        submit: "Submit",
      },
      {
        name: "SpXOverlayDeletionWorkflow",
        title: "New SpX Overlay Deletion Workflow",
        labels: ["Site *", "Overlay ID *", "Namespace Tag"],
        submit: "Submit",
      },
    ];
    for (const { name, title, labels, submit } of forms) {
      await page.goto(
        `/workflows/new/${formIdOf(name as keyof typeof workflowFormIds)}`
      );
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
      for (const label of labels) {
        await expect(page.getByText(label, { exact: true })).toBeVisible();
      }
      await expect(page.getByRole("button", { name: submit })).toBeEnabled();
      await expect(page.getByText(/^Could not load .* options/)).toHaveCount(0);
    }

    await page.goto("/workflows/new/site-cable-validation");
    await expect(
      picker(
        page,
        `${STATUS_LIST.active}, ${STATUS_LIST.provisioned}. Open options`
      )
    ).toBeVisible();

    await page.goto("/workflows/new/spx-overlay-deletion");
    await expect(picker(page, "Select a Overlay ID...")).toBeDisabled();
    await expect(picker(page, "spectrumx. Open options")).toBeVisible();
    const overlaysRequest = page.waitForRequest(
      (request) => new URL(request.url()).pathname === "/v1/parameter/overlay"
    );
    await choose(page, "Select a Site...", SITES_LIST.pdx01);
    expect(queryOf(await overlaysRequest)).toEqual([
      ["isolation_type", "spectrum_x_vrf"],
      ["location", SITES_LIST.pdx01],
      ["location_type", "Site"],
    ]);
    await expect(picker(page, "Select a Overlay ID...")).toBeEnabled();
  });

  test("IBPKeyMemberUpdateWorkflow renders its generic row variants", async ({
    page,
  }) => {
    await page.goto("/workflows/new/ib-pkey-member-update");

    await expect(
      page.getByRole("heading", {
        name: "New InfiniBand PKey Member Update Workflow",
      })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Replace Members" })
    ).toBeVisible();
    await expect(
      page.getByText("Any current members not present here will be removed.", {
        exact: false,
      })
    ).toBeVisible();
    await expect(
      page.getByText("This workflow cannot be started from this form")
    ).toHaveCount(0);
  });

  test("an unknown workflow, or a form the server does not have, is not found", async ({
    page,
  }) => {
    const requests = formRequests(page);
    await page.goto("/workflows/new/NoSuchWorkflow");
    await expect(
      page.getByRole("heading", { name: "Workflow not found" })
    ).toBeVisible();
    await expect(
      page.getByText(
        'Workflow "NoSuchWorkflow" was not found or is not available through the API.'
      )
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Return to Workflows" })
    ).toHaveAttribute("href", "/workflows");

    // The form ID is decoded from the path, but unknown catalog entries are rejected
    // without probing a generic-form endpoint.
    await page.goto("/workflows/new/No%20Such%2FWorkflow");
    await expect(
      page.getByText('Workflow "No Such/Workflow" was not found', {
        exact: false,
      })
    ).toBeVisible();
    expect(requests).toEqual([]);

    // In the catalog, but /form answers 404 (e.g. disabled for the API since).
    await page.route("**/v1/workflow/deploy/form", (route) =>
      route.fulfill({
        status: 404,
        json: { detail: "Workflow 'DeployWorkflow' not found" },
      })
    );
    await page.goto("/workflows/new/deploy");
    await expect(
      page.getByRole("heading", { name: "Workflow not found" })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit" })).toHaveCount(0);
    expect(requests).toEqual(["/v1/workflow/deploy/form"]);
  });

  test("a workflow disabled for browser forms cannot be opened directly", async ({
    page,
  }) => {
    const requests = formRequests(page);

    await page.goto("/workflows/new/spx-overlay-assignment");

    await expect(
      page.getByRole("heading", { name: "Workflow not found" })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit" })).toHaveCount(0);
    expect(requests).toEqual([]);
  });

  test("an older API that omits form metadata asks for an upgrade without probing /form", async ({
    page,
  }) => {
    const requests = formRequests(page);
    await page.route("**/v1/workflow/metadata?include=form", (route) =>
      route.fulfill({
        status: 200,
        json: {
          workflows: SERVER_WORKFLOW_METADATA.workflows.map(
            ({ form_id: _formId, has_form: _hasForm, ...workflow }) => workflow
          ),
        },
      })
    );

    await page.goto("/workflows/new/deploy");

    await expect(
      page.getByRole("heading", { name: "Workflow API upgrade required" })
    ).toBeVisible();
    const alert = page
      .getByRole("alert")
      .filter({ hasText: "Browser workflow forms" });
    await expect(alert).toContainText(API_UPGRADE_REQUIRED);
    await expect(alert).toContainText(
      "You can still start the workflow through the API or CLI."
    );
    await expect(page.getByRole("button", { name: "Submit" })).toHaveCount(0);
    expect(requests).toEqual([]);
  });

  test("a form with an unsupported ui_schema_version is not rendered", async ({
    page,
  }) => {
    await page.route("**/v1/workflow/deploy/form", (route) =>
      route.fulfill({
        status: 200,
        json: {
          ...(SERVER_WORKFLOW_FORMS.DeployWorkflow as object),
          ui_schema_version: 2,
        },
      })
    );
    await page.goto("/workflows/new/deploy");

    await expect(
      page.getByRole("heading", { name: DEPLOY_TITLE })
    ).toBeVisible();
    const alert = page
      .getByRole("alert")
      .filter({ hasText: "This form needs a newer UI" });
    await expect(alert).toContainText(
      "This form uses UI schema version 2, but this UI supports version 1. Upgrade the " +
        "Config Manager UI to run this workflow from the browser."
    );
    await expect(alert).toContainText(
      "You can still start it through the API or CLI."
    );
    await expect(page.getByRole("button", { name: "Submit" })).toHaveCount(0);
  });

  test("a form requiring a capability this UI lacks is not rendered", async ({
    page,
  }) => {
    const deploy = SERVER_WORKFLOW_FORMS.DeployWorkflow as {
      requires: string[];
    };
    await page.route("**/v1/workflow/deploy/form", (route) =>
      route.fulfill({
        status: 200,
        json: {
          ...deploy,
          requires: [...deploy.requires, "core-field.rack-picker.v1"],
        },
      })
    );
    await page.goto("/workflows/new/deploy");

    const alert = page
      .getByRole("alert")
      .filter({ hasText: "This form needs a newer UI" });
    await expect(alert).toContainText(
      "This form needs a capability this UI does not support (core-field.rack-picker.v1)."
    );
    await expect(page.getByRole("button", { name: "Submit" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(
      0
    );
    await expect(
      page.getByRole("link", { name: "Return to Workflows" })
    ).toBeVisible();
  });

  test("an unavailable plugin form (503) shows its generic diagnostic, without Try again", async ({
    page,
  }) => {
    const diagnostic =
      "This workflow form is unavailable because its plugin failed form validation.";
    await page.route("**/v1/workflow/deploy/form", (route) =>
      route.fulfill({
        status: 503,
        json: {
          detail: {
            code: "workflow_form_unavailable",
            plugin: "acme-workflows",
            workflow: "DeployWorkflow",
            message: diagnostic,
          },
        },
      })
    );
    await page.goto("/workflows/new/deploy");

    const alert = page
      .getByRole("alert")
      .filter({ hasText: "This form is unavailable" });
    await expect(alert).toContainText(
      'The form of this workflow from plugin "acme-workflows"'
    );
    await expect(alert).toContainText(
      "You can still start the workflow through the API or CLI."
    );
    await expect(alert).toContainText(diagnostic);
    await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(
      0
    );
    await expect(
      page.getByRole("link", { name: "Return to Workflows" })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit" })).toHaveCount(0);
  });

  test("a failed form request can be retried", async ({ page }) => {
    let failures = 1;
    await page.route("**/v1/workflow/deploy/form", (route) =>
      failures-- > 0
        ? route.fulfill({ status: 500, json: { detail: "boom" } })
        : route.fallback()
    );
    await page.goto("/workflows/new/deploy");

    await expect(
      page.getByText(
        "The workflow API returned HTTP 500 while loading the form."
      )
    ).toBeVisible();
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("button", { name: "Submit" })).toBeVisible();
  });

  test("a user without an execute role sees the form disabled, with the launcher's reason", async ({
    page,
  }) => {
    await mockServerCatalogAndUser(page, ["reader"]);
    await page.goto("/workflows/new/deploy");

    await expect(
      page.getByRole("heading", { name: DEPLOY_TITLE })
    ).toBeVisible();
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "You cannot start this workflow" })
    ).toContainText(DEPLOY_ROLES_REASON);
    await expect(
      page.locator("fieldset[disabled]")
    ).toHaveAccessibleDescription(DEPLOY_ROLES_REASON);
    await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
    await expect(picker(page, "Select a Site...")).toBeDisabled();
    await expect(
      page.getByRole("checkbox", { name: "Use commit-confirm" })
    ).toBeDisabled();

    await page.getByRole("button", { name: "New workflow" }).click();
    const launcherEntry = page
      .getByRole("dialog")
      .getByRole("button", { name: "Configuration Deploy", exact: true });
    await launcherEntry.hover();
    await expect(launcherEntry).toHaveAccessibleDescription(
      DEPLOY_ROLES_REASON
    );
  });

  test("an authorization failure from /whoami disables the form", async ({
    page,
  }) => {
    await page.route("**/whoami", (route) =>
      route.fulfill({ status: 403, json: { error: "Forbidden" } })
    );
    await page.goto("/workflows/new/deploy");

    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "You cannot start this workflow" })
    ).toContainText("Unauthorized");
    await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
  });

  test("a transient /whoami failure leaves the form enabled and can be retried", async ({
    page,
  }) => {
    const failWhoami = (route: Route) =>
      route.fulfill({ status: 503, json: { error: "Unavailable" } });
    await page.route("**/whoami", failWhoami);
    await page.goto("/workflows/new/deploy");

    const warning = page
      .getByRole("alert")
      .filter({ hasText: "Permissions could not be verified" });
    await expect(warning).toContainText(
      "The server will verify your permission when you submit it."
    );
    await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
    await expect(picker(page, "Select a Site...")).toBeEnabled();
    await expect(page.locator("fieldset[disabled]")).toHaveCount(0);

    // Fall back to the successful /whoami handler installed by beforeEach.
    await page.unroute("**/whoami", failWhoami);
    await page.getByRole("button", { name: "Retry permission check" }).click();
    await expect(warning).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
  });
});

/**
 * Previously shipped form URLs, including those Nautobot hardcodes in
 * `components/nautobot/.../templates/nv_config_manager/`.
 */
const COHORT_1_REDIRECTS: {
  name: string;
  case: string;
  legacyUrl: string;
  /** Checks the form on the form ID route shows the query's values. */
  prefilled: (page: Page) => Promise<void>;
}[] = [
  {
    name: "IBPKeyCreationWorkflow",
    case: "host and pkey",
    legacyUrl:
      "/workflows/ibpkeycreationworkflow/form?host=ufm.example.com&pkey=0x0100",
    prefilled: async (page) => {
      await expect(page.getByLabel("UFM Host")).toHaveValue("ufm.example.com");
      await expect(page.getByLabel("PKey (optional)")).toHaveValue("0x0100");
    },
  },
  {
    name: "IBPKeyMemberUpdateWorkflow",
    case: "host and pkey",
    legacyUrl:
      "/workflows/ibpkeymemberupdateworkflow/form?host=ufm-1.lab&pkey=0x8001",
    prefilled: async (page) => {
      await expect(page.getByLabel("UFM Host")).toHaveValue("ufm-1.lab");
      await expect(page.getByLabel("PKey")).toHaveValue("0x8001");
    },
  },
  {
    name: "SpXOverlayDeletionWorkflow",
    case: "site, overlay, and namespace alias",
    legacyUrl:
      `/workflows/spxoverlaydeletionworkflow/form?site=${SITES_LIST.pdx01}` +
      "&overlay_id=test-overlay-1&namespace=tenant-a",
    prefilled: async (page) => {
      await expect(
        picker(page, `${SITES_LIST.pdx01}. Open options`)
      ).toBeVisible();
      await expect(picker(page, "test-overlay-1. Open options")).toBeVisible();
      await expect(picker(page, "tenant-a. Open options")).toBeVisible();
    },
  },
  {
    // configmanagerdevicestatus_workflows_tab.html:46 and inc/pending_deployment.html:5:
    // ?site={{ site }}&device-id={{ device_id }}
    name: "DeployWorkflow",
    case: "Nautobot link",
    legacyUrl: `/workflows/deployworkflow/form?site=${SITES_LIST.pdx01}&device-id=${DEVICE.id}`,
    prefilled: async (page) => {
      await expect(
        picker(page, `${SITES_LIST.pdx01}. Open options`)
      ).toBeVisible();
      await expect(picker(page, `${DEVICE.name}. Open options`)).toBeVisible();
    },
  },
  {
    // A repeated device-id passes through unchanged; the device picker takes the first.
    name: "DeployWorkflow",
    case: "repeated device-id",
    legacyUrl:
      `/workflows/deployworkflow/form?site=${SITES_LIST.pdx01}` +
      `&device-id=${DEVICE.id}&device-id=${
        DEVICES_LIST[SITES_LIST.pdx01][1].id
      }`,
    prefilled: async (page) => {
      await expect(picker(page, `${DEVICE.name}. Open options`)).toBeVisible();
    },
  },
  {
    // configmanagerdevicestatus_workflows_tab.html:92:
    // ?site={{ site }}&device-id={{ device_id }}&tenant={{ tenant }}
    name: "SiteCableValidationWorkflow",
    case: "Nautobot link",
    legacyUrl:
      `/workflows/sitecablevalidationworkflow/form?site=${SITES_LIST.pdx01}` +
      `&device-id=${DEVICE.id}&tenant=${TENANT_LIST.ngc}`,
    prefilled: async (page) => {
      await expect(
        picker(page, `${SITES_LIST.pdx01}. Open options`)
      ).toBeVisible();
      await expect(
        picker(page, `${TENANT_LIST.ngc}. Open options`)
      ).toBeVisible();
    },
  },
  {
    // The same template for a device without a tenant, at a location whose name has a
    // space (the template does not URL-encode it) and differs from its id.
    name: "SiteCableValidationWorkflow",
    case: "Nautobot link, unencoded module name, empty tenant",
    legacyUrl: `/workflows/sitecablevalidationworkflow/form?site=PDX01 Pod 1&device-id=${DEVICE.id}&tenant=`,
    prefilled: async (page) => {
      await expect(picker(page, "PDX01 Pod 1. Open options")).toBeVisible();
      await expect(picker(page, "Select a Tenant...")).toBeVisible();
    },
  },
];

test.describe("legacy form URLs", () => {
  test.beforeEach(async ({ page }) => {
    await mockServerCatalogAndUser(page, ["reader", "executor"]);
    await mockTypedLocationsEndpoint(page);
  });

  for (const {
    name,
    case: title,
    legacyUrl,
    prefilled,
  } of COHORT_1_REDIRECTS) {
    test(`${name} legacy URL (${title}) redirects with its query and prefills the form`, async ({
      page,
    }) => {
      const legacy = new URL(legacyUrl, "http://ui.test");
      const forms = formRequests(page);

      const response = await page.request.get(legacyUrl, { maxRedirects: 0 });
      expect(response.status()).toBe(307);
      const location = new URL(response.headers()["location"], legacy);
      const formId = formIdOf(name as keyof typeof workflowFormIds);
      expect(location.pathname).toBe(`/workflows/new/${formId}`);
      expect(location.search).toBe(legacy.search);

      const landed = await page.goto(legacyUrl);
      expect(
        (await landed?.request().redirectedFrom()?.response())?.status()
      ).toBe(307);
      const url = new URL(page.url());
      expect(url.pathname).toBe(`/workflows/new/${formId}`);
      expect(url.search).toBe(legacy.search);
      await prefilled(page);
      expect(forms).toEqual([`/v1/workflow/${formId}/form`]);
    });
  }

  test("each legacy form URL redirects to its form ID route", async ({
    page,
  }) => {
    for (const [name, legacySlug] of Object.entries(legacyWorkflowRedirects)) {
      const response = await page.request.get(`/workflows/${legacySlug}/form`, {
        maxRedirects: 0,
      });

      expect(response.status(), name).toBe(307);
      expect(
        new URL(response.headers()["location"], "http://ui.test").pathname,
        name
      ).toBe(
        `/workflows/new/${formIdOf(name as keyof typeof workflowFormIds)}`
      );
    }
  });

  test("a user without the execute role sees each redirected form disabled", async ({
    page,
  }) => {
    await mockServerCatalogAndUser(page, ["reader"]);
    for (const name of new Set(COHORT_1_REDIRECTS.map((entry) => entry.name))) {
      await page.goto(
        `/workflows/new/${formIdOf(name as keyof typeof workflowFormIds)}`
      );
      await expect(
        page
          .getByRole("alert")
          .filter({ hasText: "You cannot start this workflow" })
      ).toContainText(`Required execute roles: ${name}, executor`);
      await expect(
        page.locator('fieldset[disabled] button[type="submit"]')
      ).toBeDisabled();
    }
  });
});
