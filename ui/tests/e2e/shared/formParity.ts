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
 * Golden payload parity between the retired form pages and
 * `/workflows/new/<ClassName>`. Each workflow keeps
 * `tests/e2e/fixtures/form-parity/<slug>.json`:
 *
 * ```json
 * {"workflow", "input_model", "scenarios": [
 *   {"name", "legacy_url", "new_url", "legacy_payload", "generic_payload"}]}
 * ```
 *
 * Normal runs capture the generic form's POST body and assert it against the fixture.
 * `UPDATE_PARITY=1` updates that generic capture while preserving the legacy golden.
 * `packages/workflows/tests/workflows/test_form_parity.py` checks that both payloads
 * are equal after `InputModel.model_validate()`.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { expect, type Locator, type Page } from "@playwright/test";

import type { JsonObject } from "@/types/workflow-catalog.types";

const UI_ROOT = join(__dirname, "../../..");
const FIXTURE_DIR = join(UI_ROOT, "tests/e2e/fixtures/form-parity");
const LEGACY_WORKFLOW_REDIRECTS: Record<string, string> = JSON.parse(
  readFileSync(join(UI_ROOT, "src/config/legacy-workflow-redirects.json"), "utf8")
);

/** Write captured payloads into the fixtures instead of asserting them. */
export const UPDATE_PARITY = process.env.UPDATE_PARITY === "1";

/** Fills the generic form by its visible labels. */
export interface FormDriver {
  page: Page;
  /** Pick options (by label) in the select or multiselect labelled `label`. */
  select(label: string, ...options: string[]): Promise<void>;
  /** Wait until the select labelled `label` shows exactly `options` (in option order). */
  expectSelected(label: string, ...options: string[]): Promise<void>;
  fill(label: string, value: string): Promise<void>;
  setChecked(label: string, checked: boolean): Promise<void>;
}

export interface ParityScenario {
  name: string;
  /** Query string, with its `?`, for both routes; e.g. a Nautobot link's. */
  query?: string;
  /** Fill the form; it must wait for any prefill it relies on. */
  steps?: (form: FormDriver) => Promise<void>;
}

export interface ParityWorkflow {
  /** Workflow class name. */
  workflow: string;
  inputModel: string;
  /** Submit endpoint path, e.g. `/v1/workflow/ngc/deploy`. */
  endpoint: string;
  scenarios: ParityScenario[];
}

interface FixtureScenario {
  name: string;
  legacy_url: string;
  new_url: string;
  legacy_payload: JsonObject | null;
  generic_payload: JsonObject | null;
}

interface ParityFixture {
  workflow: string;
  input_model: string;
  scenarios: FixtureScenario[];
}

const legacySlugOf = (workflow: string) => {
  const legacySlug = LEGACY_WORKFLOW_REDIRECTS[workflow];
  if (!legacySlug) {
    throw new Error(`${workflow} is not in legacy-workflow-redirects.json`);
  }
  return legacySlug;
};

export const scenarioUrls = (workflow: string, scenario: ParityScenario) => ({
  legacy_url: `/workflows/${legacySlugOf(workflow)}/form${scenario.query ?? ""}`,
  new_url: `/workflows/new/${encodeURIComponent(workflow)}${scenario.query ?? ""}`,
});

const fixturePath = (workflow: string) =>
  join(FIXTURE_DIR, `${legacySlugOf(workflow)}.json`);

export const readParityFixture = (workflow: string): ParityFixture | undefined => {
  const path = fixturePath(workflow);
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : undefined;
};

/** Store one capture, keeping the definition's scenario order and dropping stale ones. */
const recordPayload = (
  definition: ParityWorkflow,
  scenario: ParityScenario,
  payload: JsonObject
) => {
  const stored = readParityFixture(definition.workflow);
  const byName = new Map(stored?.scenarios.map((entry) => [entry.name, entry]));
  const current = byName.get(scenario.name);
  byName.set(scenario.name, {
    name: scenario.name,
    ...scenarioUrls(definition.workflow, scenario),
    legacy_payload: current?.legacy_payload ?? null,
    generic_payload: payload,
  });
  const fixture: ParityFixture = {
    workflow: definition.workflow,
    input_model: definition.inputModel,
    scenarios: definition.scenarios
      .map(({ name }) => byName.get(name))
      .filter((entry): entry is FixtureScenario => entry !== undefined),
  };
  mkdirSync(FIXTURE_DIR, { recursive: true });
  writeFileSync(fixturePath(definition.workflow), `${JSON.stringify(fixture, null, 2)}\n`);
};

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A field label's text: `label`, with or without the required marker. */
const labelText = (label: string) => new RegExp(`^${escapeRegExp(label)}( \\*)?$`);

/** A select's trigger: the first button in the field its label heads. */
const selectTrigger = (page: Page, label: string): Locator =>
  page
    .locator("form")
    .getByText(labelText(label))
    .locator("..")
    .getByRole("button")
    .first();

/** Drives a form by its visible labels (see {@link FormDriver}). */
export const formDriver = (page: Page): FormDriver => ({
  page,
  async select(label, ...options) {
    const trigger = selectTrigger(page, label);
    const dialog = page.getByRole("dialog");
    const optionLocator = (option: string) =>
      dialog
        .getByRole("option")
        .filter({ has: page.getByText(option, { exact: true }) });
    // A field that starts loading re-renders and can close a popover opened just
    // before, so reopen until the first option shows. Enter, not a click: selected-item
    // chips cover the middle of a multiselect.
    await expect(async () => {
      if (!(await dialog.isVisible())) {
        await expect(trigger).toBeEnabled({ timeout: 1000 });
        await trigger.press("Enter", { timeout: 1000 });
      }
      await expect(optionLocator(options[0])).toBeVisible({ timeout: 1000 });
    }).toPass();
    for (const option of options) {
      await optionLocator(option).click();
    }
    // A single select closes on pick; a multiselect stays open.
    if (await dialog.isVisible()) await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  },
  async expectSelected(label, ...options) {
    await expect(selectTrigger(page, label)).toHaveAttribute(
      "aria-label",
      `${options.join(", ")}. Open options`
    );
  },
  async fill(label, value) {
    await page.getByLabel(labelText(label)).fill(value);
  },
  async setChecked(label, checked) {
    await page.getByRole("checkbox", { name: label, exact: true }).setChecked(checked);
  },
});

/**
 * Open the scenario on the generic route, run its steps, submit, and return the POST
 * body. The submit endpoint is the shared Playwright mock, so the request completes.
 */
export const capturePayload = async (
  page: Page,
  definition: ParityWorkflow,
  scenario: ParityScenario
): Promise<JsonObject> => {
  const urls = scenarioUrls(definition.workflow, scenario);
  const response = await page.goto(urls.new_url);
  expect(response?.request().redirectedFrom() ?? null).toBeNull();
  await expect(page.locator('form button[type="submit"]')).toBeVisible();

  await scenario.steps?.(formDriver(page));

  const submitted = page.waitForRequest(
    (request) =>
      request.method() === "POST" && new URL(request.url()).pathname === definition.endpoint
  );
  await page.locator('form button[type="submit"]').click();
  return (await submitted).postDataJSON() as JsonObject;
};

/** Assert a capture against the fixture, or record it with `UPDATE_PARITY=1`. */
export const checkPayload = (
  definition: ParityWorkflow,
  scenario: ParityScenario,
  payload: JsonObject
) => {
  if (UPDATE_PARITY) {
    recordPayload(definition, scenario, payload);
    return;
  }
  const stored = readParityFixture(definition.workflow)?.scenarios.find(
    (entry) => entry.name === scenario.name
  );
  const hint = "re-capture with UPDATE_PARITY=1";
  expect(stored, `no fixture entry for "${scenario.name}"; ${hint}`).toBeDefined();
  expect(
    { legacy_url: stored!.legacy_url, new_url: stored!.new_url },
    `scenario URLs changed; ${hint}`
  ).toEqual(scenarioUrls(definition.workflow, scenario));
  expect(payload, `generic payload differs from the fixture; ${hint} if intended`).toEqual(
    stored!.generic_payload
  );
};
