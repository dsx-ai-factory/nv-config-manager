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
import { expect } from "@playwright/test";
import { providerLogoutRedirect } from "../../src/lib/auth/logout";
import { test } from "./shared/utils";

const gatewayUrl = new URL(
  process.env.OIDC_GATEWAY_URL || "http://localhost:3000"
);
const gatewayHome = new URL("/", gatewayUrl).toString();
const applicationUrl = new URL(gatewayUrl);
applicationUrl.hostname = `nautobot.${gatewayUrl.hostname}`;
const applicationHome = new URL("/", applicationUrl).toString();
const providerEndSessionUrl = "https://idp.example.com/oidc/logout";
const providerReturnUrl = "https://nautobot.config-manager.example.com/";
const LONG_USERNAME =
  "thisusernameisintentionallylongenoughtoexceedtheusercolumnwidth";

const providerLogoutUrl = (clientId?: string): URL => {
  const redirect = providerLogoutRedirect(
    providerReturnUrl,
    providerEndSessionUrl,
    clientId
  );
  return new URL(new URL(redirect, gatewayUrl).searchParams.get("rd")!);
};

test.describe("Workflows Page", () => {
  test("logout builds a provider end-session redirect", () => {
    const url = providerLogoutUrl("nv-config-manager");

    expect(url.origin).toBe("https://idp.example.com");
    expect(url.pathname).toBe("/oidc/logout");
    expect(url.searchParams.get("post_logout_redirect_uri")).toBe(
      providerReturnUrl
    );
    expect(url.searchParams.get("client_id")).toBe("nv-config-manager");
    expect(url.searchParams.get("id_token_hint")).toBe("{id_token}");
  });

  test("logout omits the optional provider client ID", () => {
    expect(providerLogoutUrl().searchParams.has("client_id")).toBe(false);
  });

  test("logout returns to the base hostname by default", async ({ request }) => {
    const response = await request.get("/auth/logout", { maxRedirects: 0 });

    expect(response.status()).toBe(302);
    expect(response.headers().location).toBe(
      `/oauth2/sign_out?rd=${encodeURIComponent(gatewayHome)}`
    );
  });

  test("logout accepts a return URL on a gateway subdomain", async ({ request }) => {
    const response = await request.get(
      `/auth/logout?rd=${encodeURIComponent(applicationHome)}`,
      { maxRedirects: 0 }
    );

    expect(response.status()).toBe(302);
    expect(response.headers().location).toBe(
      `/oauth2/sign_out?rd=${encodeURIComponent(applicationHome)}`
    );
  });

  test("logout rejects a return URL outside the gateway domain", async ({ request }) => {
    const response = await request.get(
      "/auth/logout?rd=https%3A%2F%2Fexample.com%2F",
      { maxRedirects: 0 }
    );

    expect(response.status()).toBe(302);
    expect(response.headers().location).toBe(
      `/oauth2/sign_out?rd=${encodeURIComponent(gatewayHome)}`
    );
  });

  test("logout expires cookies sent by the site", async ({ request }) => {
    const response = await request.get("/auth/logout", {
      headers: {
        Cookie:
          "NVConfigManagerAccessToken=access; _nvcm_oidc_proxy=session; azureSession=session; appPreference=compact",
      },
      maxRedirects: 0,
    });
    const setCookieHeaders = response
      .headersArray()
      .filter((header) => header.name.toLowerCase() === "set-cookie")
      .map((header) => header.value);

    expect(
      setCookieHeaders.some((header) =>
        header.startsWith("NVConfigManagerAccessToken=;")
      )
    ).toBe(true);
    expect(
      setCookieHeaders.some((header) =>
        header.startsWith("NVConfigManagerIdToken=;")
      )
    ).toBe(true);
    expect(
      setCookieHeaders.some((header) => header.startsWith("azureSession=;"))
    ).toBe(true);
    expect(
      setCookieHeaders.some((header) =>
        header.startsWith("_nvcm_oidc_proxy=;")
      )
    ).toBe(false);
    expect(
      setCookieHeaders.some((header) => header.startsWith("appPreference=;"))
    ).toBe(true);
  });

  test("renders a unified metadata-backed workflow table", async ({ page }) => {
    await page.goto("/workflows?workflow_type=DeployWorkflow");

    await expect(
      page.getByRole("heading", { name: "Workflows" })
    ).toBeVisible();
    await expect(
      page.getByText("New Workflow", { exact: true })
    ).toHaveCount(0);

    await expect(
      page.getByRole("cell", { name: "Configuration Deploy" })
    ).toBeVisible();
    await expect(page.getByText("LEAF2-GP1-CIN2-PDX01")).toBeVisible();
    const tableHeader = page.locator("thead");
    await expect(tableHeader.getByText("Roles")).toHaveCount(0);
    await expect(tableHeader.getByText("Device ID")).toHaveCount(0);
    await expect(tableHeader.getByText("Device Role")).toHaveCount(0);
    await expect(tableHeader.getByText("Device Platform")).toHaveCount(0);

    await page.getByRole("button", { name: "Columns" }).click();
    await page
      .getByRole("checkbox", { name: "Toggle Device ID column" })
      .click();
    await expect(tableHeader.getByText("Device ID")).toBeVisible();
  });

  test("truncates long values and exposes the full string in a hover title", async ({
    page,
  }) => {
    await page.unroute(/.*\/v1\/workflow\/?(\?.*)?$/);
    await page.route(/.*\/v1\/workflow\/?(\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          workflows: [
            {
              id: "long-content-workflow",
              workflow_type: "DeployWorkflow",
              workflow_input: {},
              started_by: LONG_USERNAME,
              start_time: "2025-03-04T02:32:38.087457Z",
              close_time: null,
              status: "RUNNING",
              pending_approval: false,
              search_attributes: {
                Site: ["PDX01"],
                DeviceName: ["LEAF2-GP1-CIN2-PDX01"],
                User: [LONG_USERNAME],
              },
              href: "https://temporal.example.com/long-content-workflow",
            },
          ],
          next_page_token: null,
          total_count: 1,
          page_count: 1,
        },
      });
    });

    await page.goto("/workflows");

    const workflowRow = page.locator("tbody tr").first();
    const userCell = workflowRow.locator("td").filter({
      hasText: LONG_USERNAME,
    });
    const userValue = userCell.getByTitle(LONG_USERNAME, { exact: true });

    await expect(userValue).toHaveCSS("overflow", "hidden");
    await expect(userValue).toHaveCSS("text-overflow", "ellipsis");
    await expect(userValue).toHaveCSS("white-space", "nowrap");
    await userValue.hover();

    const cellsContainOverflow = await workflowRow.locator("td").evaluateAll(
      (cells) =>
        cells.every((cell) => getComputedStyle(cell).overflow === "hidden"),
    );
    expect(cellsContainOverflow).toBe(true);

    const cellBox = await userCell.boundingBox();
    const valueBox = await userValue.boundingBox();
    expect((valueBox?.x ?? 0) + (valueBox?.width ?? 0)).toBeLessThanOrEqual(
      (cellBox?.x ?? 0) + (cellBox?.width ?? 0),
    );
  });

  test("terminal status takes precedence over stale stage flags", async ({
    page,
  }) => {
    await page.unroute(/.*\/v1\/workflow\/?(\?.*)?$/);
    await page.route(/.*\/v1\/workflow\/?(\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          workflows: [
            {
              id: "terminated-pending-workflow",
              workflow_type: "DeployWorkflow",
              workflow_input: {},
              started_by: "test",
              start_time: "2025-03-04T02:32:38.087457Z",
              close_time: "2025-03-04T02:33:38.087457Z",
              status: "TERMINATED",
              pending_approval: true,
              failed_stage: false,
              search_attributes: { PendingApproval: [true], User: ["test"] },
              href: "https://temporal.example.com/terminated-pending-workflow",
            },
          ],
          next_page_token: null,
          total_count: 1,
          page_count: 1,
        },
      });
    });

    await page.goto("/workflows");

    const workflowRow = page.locator("tbody tr").first();
    await expect(
      workflowRow.getByText("Terminated", { exact: true })
    ).toBeVisible();
    await expect(
      workflowRow.getByText("Pending Approval", { exact: true })
    ).toHaveCount(0);
  });

  test("shows user roles and disables workflows the user cannot execute", async ({
    page,
  }) => {
    await page.goto("/workflows?workflow_type=DeployWorkflow");

    await page.getByRole("button", { name: "User roles" }).click();
    await expect(page.getByText("Username", { exact: true })).toBeVisible();
    await expect(page.getByText("joliao@nvidia.com")).toBeVisible();
    await expect(page.getByText("Roles", { exact: true })).toBeVisible();
    await expect(page.getByText("nvcm-network")).toBeVisible();
    await expect(page.getByText("all", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Logout" })).toHaveAttribute(
      "href",
      "/auth/logout"
    );

    await page.getByRole("button", { name: "New workflow" }).click();
    await expect(
      page.getByRole("link", { name: "Configuration Deploy" })
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Multi-Configuration Deploy" })
    ).toHaveCount(0);

    await page.getByText("Multi-Configuration Deploy").hover();
    await expect(
      page.getByRole("tooltip", {
        name: "Required execute roles: nvcm-admin",
      })
    ).toBeVisible();
  });

  test("shows unauthorized when workflow whoami cannot be accessed", async ({
    page,
  }) => {
    await page.unroute("**/whoami");
    await page.route("**/whoami", async (route) => {
      await route.fulfill({
        status: 403,
        json: { error: "Forbidden" },
      });
    });

    await page.goto("/workflows?workflow_type=DeployWorkflow");

    await page.getByRole("button", { name: "User roles" }).click();
    await expect(page.getByText("Unauthorized", { exact: true })).toBeVisible();
    await expect(page.getByText("Unknown user", { exact: true })).toHaveCount(0);
    await expect(
      page.getByText("Unable to load roles", { exact: true })
    ).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Logout" })).toHaveAttribute(
      "href",
      "/auth/logout"
    );

    await page.getByRole("button", { name: "New workflow" }).click();
    await expect(
      page.getByRole("link", { name: "Configuration Deploy" })
    ).toHaveCount(0);
    await page.getByText("Multi-Configuration Deploy").hover();
    await expect(
      page.getByRole("tooltip", { name: "Unauthorized" }).first()
    ).toBeVisible();
  });

  test("keeps rows visible when loading the next backend page", async ({
    page,
  }) => {
    await page.goto("/workflows");

    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();
    await page.getByRole("button", { exact: true, name: "Next" }).click();

    await expect(page.getByText(/Page 2 of \d+/)).toBeVisible();
    await expect(page.getByText(/\d+ workflows/)).toBeVisible();
    await expect(page.locator("tbody tr").first()).toBeVisible();
  });

  test("applies workflow ID filtering through the backend from page 2", async ({
    page,
  }) => {
    await page.goto("/workflows");
    await page.getByRole("button", { exact: true, name: "Next" }).click();
    await expect(page.getByText(/Page 2 of \d+/)).toBeVisible();

    const workflowId = (
      await page.locator("tbody tr").last().locator("a").first().innerText()
    ).trim();
    const filteredResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());

      return (
        url.pathname.endsWith("/v1/workflow/") &&
        url.searchParams.get("workflow_id") === workflowId &&
        !url.searchParams.has("next_page_token")
      );
    });

    await page
      .locator("thead")
      .getByRole("textbox", { name: "Search..." })
      .first()
      .fill(workflowId);
    await filteredResponse;

    await expect(page.getByText("Page 1 of 1")).toBeVisible();
    await expect(page.getByText("1 workflow", { exact: true })).toBeVisible();
    await expect(page.locator("tbody").getByText(workflowId)).toBeVisible();
  });

  test("uses exact case-sensitive workflow attribute filters", async ({
    page,
  }) => {
    await page.goto("/workflows");

    const siteFilter = page
      .locator("thead th")
      .filter({ hasText: "Site" })
      .getByRole("textbox");

    await siteFilter.fill("rno1");
    await expect(page.getByText("No results.")).toBeVisible();

    await siteFilter.fill("RNO1");
    await expect(page.getByText("RNO1").first()).toBeVisible();
    await page
      .locator("thead th")
      .filter({ hasText: "Device Name" })
      .getByRole("textbox")
      .fill("rno1-m04-c10-core1-cg1-tan-lab1");

    await expect(page.getByText("RNO1").first()).toBeVisible();
    await expect(page.getByText("rno1-m04-c10-core1-cg1-tan-lab1").first()).toBeVisible();
    await expect(page.getByText("No results.")).toHaveCount(0);
  });

  test("uses backend hide-completed filtering and disables header sorting", async ({
    page,
  }) => {
    await page.goto("/workflows");

    const hideCompletedResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());

      return (
        url.pathname.endsWith("/v1/workflow/") &&
        url.searchParams.get("hide_completed") === "true"
      );
    });
    await page.getByRole("checkbox", { name: "Hide completed workflows" }).click();
    const hiddenResponse = await hideCompletedResponse;
    const hiddenResponseUrl = new URL(hiddenResponse.url());
    const hiddenPageSize = Number(hiddenResponseUrl.searchParams.get("limit") ?? "10");
    const hiddenResponseBody = (await hiddenResponse.json()) as {
      page_count: number;
      total_count: number;
      workflows: { status: string }[];
    };

    expect(
      hiddenResponseBody.workflows.every((workflow) => workflow.status !== "COMPLETED")
    ).toBe(true);
    expect(hiddenResponseBody.page_count).toBe(
      hiddenResponseBody.total_count === 0
        ? 0
        : Math.ceil(hiddenResponseBody.total_count / hiddenPageSize)
    );

    await expect(
      page.locator("thead").getByRole("button", { name: /Workflow Type/ })
    ).toHaveCount(0);
  });

  test("supports value filter icons through workflow list filters", async ({
    page,
  }) => {
    await page.goto("/workflows?workflow_type=DeployWorkflow");

    const deviceNameFilter = page
      .getByRole("link", {
        name: "Filter by Device Name: LEAF2-GP1-CIN2-PDX01",
      })
      .first();

    await deviceNameFilter.click();
    await expect(page).toHaveURL(/workflow_type=DeployWorkflow/);
    await expect(page).toHaveURL(/device_name=LEAF2-GP1-CIN2-PDX01/);
    await expect(page.getByText("LEAF2-GP1-CIN2-PDX01")).toBeVisible();

    const removeDeviceNameFilter = page.getByRole("link", {
      name: "Remove Device Name filter: LEAF2-GP1-CIN2-PDX01",
    });

    await expect(removeDeviceNameFilter).toHaveCSS("border-bottom-width", "0px");
    await removeDeviceNameFilter.click();

    await expect(page).toHaveURL(/\/workflows\?workflow_type=DeployWorkflow$/);
    await expect(deviceNameFilter).toBeVisible();
  });

  test("hydrates status filters from the URL", async ({ page }) => {
    await page.goto("/workflows?status=FAILED");

    await expect(page.getByText("Device Cable Validation")).toBeVisible();
    await expect(page.getByText("LEAF2-GP1-CIN3-PDX01")).toBeVisible();
    await expect(
      page.locator("tbody").getByText("Failed", { exact: true })
    ).toBeVisible();

    await page.goto("/workflows?status=Completed");
    const statusFilter = page
      .locator("thead")
      .getByRole("cell", { name: /Status/ })
      .getByRole("combobox");

    await expect(statusFilter).toContainText("Completed");
    await expect(page).toHaveURL(/status=COMPLETED/);
    await expect(
      page.locator("tbody").getByText("Completed", { exact: true }).first()
    ).toBeVisible();

    await statusFilter.click();
    await page.getByRole("option", { name: "All" }).click();

    await expect(page).toHaveURL(/\/workflows$/);
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();
  });

  test("supports dropdown filters and clearing all filters", async ({ page }) => {
    await page.goto("/workflows");

    await page
      .locator("thead")
      .getByRole("cell", { name: /Status/ })
      .getByRole("combobox")
      .click();
    await page.getByRole("option", { name: "Failed" }).click();

    await expect(page.getByText("Device Cable Validation")).toBeVisible();
    await expect(page.getByText("LEAF2-GP1-CIN3-PDX01")).toBeVisible();

    await page.getByRole("button", { name: "Clear All Filters" }).click();
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();

    const pendingApprovalResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());

      return (
        url.pathname.endsWith("/v1/workflow/") &&
        url.searchParams.get("status") === "RUNNING" &&
        url.searchParams.get("pending_approval") === "true"
      );
    });
    await page
      .locator("thead")
      .getByRole("cell", { name: /Status/ })
      .getByRole("combobox")
      .click();
    await page.getByRole("option", { name: "Pending Approval" }).click();
    await pendingApprovalResponse;

    await page.getByRole("button", { name: "Clear All Filters" }).click();
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();

    await page.goto("/workflows?status=FAILED");
    await expect(page.getByText("Device Cable Validation")).toBeVisible();
    await page.getByRole("button", { name: "Clear All Filters" }).click();

    await expect(page).toHaveURL(/\/workflows$/);
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();
  });

  test("renders the backend result set without additional status filtering", async ({
    page,
  }) => {
    await page.goto("/workflows");

    const runningResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());

      return (
        url.pathname.endsWith("/v1/workflow/") &&
        url.searchParams.get("status") === "RUNNING" &&
        !url.searchParams.has("pending_approval")
      );
    });
    await page
      .locator("thead")
      .getByRole("cell", { name: /Status/ })
      .getByRole("combobox")
      .click();
    await page.getByRole("option", { name: "Running", exact: true }).click();

    const responseBody = (await (await runningResponse).json()) as {
      workflows: unknown[];
    };
    await expect(page.locator("tbody tr")).toHaveCount(
      responseBody.workflows.length
    );
    await expect(
      page.locator("tbody").getByText("Pending Approval", { exact: true }).first()
    ).toBeVisible();
  });

  test("supports top-level workflow timeframe filters", async ({ page }) => {
    await page.goto("/workflows");

    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();

    const tableHeader = page.locator("thead");
    await expect(
      tableHeader
        .locator("th")
        .filter({ hasText: "Start Time" })
        .getByRole("textbox")
    ).toHaveCount(0);
    await expect(
      tableHeader
        .locator("th")
        .filter({ hasText: "End Time" })
        .getByRole("textbox")
    ).toHaveCount(0);

    const timeframe = await page.evaluate(() => {
      const pad = (value: number) => String(value).padStart(2, "0");
      const formatLocalDateTime = (value: string) => {
        const date = new Date(value);

        return {
          date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
            date.getDate()
          )}`,
          time: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
        };
      };

      return {
        end: formatLocalDateTime("2025-03-04T02:34:00Z"),
        start: formatLocalDateTime("2025-03-04T02:32:00Z"),
      };
    });

    await page.getByLabel("Start date").fill(timeframe.start.date);
    await page.getByLabel("Start time").fill(timeframe.start.time);
    await page.getByLabel("End date").fill(timeframe.end.date);
    await page.getByLabel("End time").fill(timeframe.end.time);

    await expect(page.getByText("Connected Host Metadata")).toBeVisible();
    await expect(
      page.getByRole("cell", { name: "Configuration Deploy" })
    ).toHaveCount(0);
    await expect(
      page.getByRole("cell", { name: "Configuration Backup" })
    ).toHaveCount(0);
    await expect(page.getByRole("cell", { name: "VPC Creation" })).toHaveCount(0);
  });

  test("keeps workflow id column width stable with no results", async ({
    page,
  }) => {
    await page.goto("/workflows");

    const workflowIdHeader = page.locator("thead th").first();
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();
    await expect(workflowIdHeader).toBeVisible();

    const initialBox = await workflowIdHeader.boundingBox();
    const initialWidth = initialBox?.width ?? 0;
    expect(initialWidth).toBeGreaterThan(280);

    await page
      .locator("thead")
      .getByRole("textbox", { name: "Search..." })
      .first()
      .fill("not-a-real-workflow-id");
    await expect(page.getByText("No results.")).toBeVisible();

    const emptyBox = await workflowIdHeader.boundingBox();
    expect(emptyBox?.width ?? 0).toBeCloseTo(initialWidth, 0);

    await page.getByRole("button", { name: "Clear All Filters" }).click();
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();

    const restoredBox = await workflowIdHeader.boundingBox();
    expect(restoredBox?.width ?? 0).toBeCloseTo(initialWidth, 0);
  });

  test("uses available viewport width for optional workflow columns", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/workflows");
    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();

    await page.getByRole("button", { name: "Columns" }).click();
    for (const columnName of [
      "Device ID",
      "Device Role",
      "Device Platform",
    ]) {
      await page
        .getByRole("checkbox", { name: `Toggle ${columnName} column` })
        .click();
    }

    const tableViewport = page.locator("table").first().locator("xpath=..");
    await expect
      .poll(async () =>
        tableViewport.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      )
      .toBe(true);

    await page.setViewportSize({ width: 1280, height: 720 });
    await expect
      .poll(async () =>
        tableViewport.evaluate(
          (element) => element.scrollWidth > element.clientWidth,
        ),
      )
      .toBe(true);
  });

  test("persists the selected row count", async ({ page }) => {
    await page.goto("/workflows");

    await expect(page.getByText("LEAF1-GP1-CIN2-PDX01").first()).toBeVisible();

    await page.getByRole("combobox").filter({ hasText: "10 Rows" }).click();
    await page.getByRole("option", { name: "50 Rows" }).click();
    await expect(
      page.getByRole("combobox").filter({ hasText: "50 Rows" })
    ).toBeVisible();

    await page.reload();

    await expect(
      page.getByRole("combobox").filter({ hasText: "50 Rows" })
    ).toBeVisible();
  });
});
