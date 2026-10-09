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
import Ajv2020, { type ErrorObject } from "ajv/dist/2020";

import { APIError, TokenError } from "@/lib/errors";
import { fetcher } from "@/lib/fetcher";
import { sanitizeUrl } from "@/lib/utils";
import capabilityManifest from "@/lib/workflow-form-v1.capabilities.json";
import wireSchema from "@/lib/workflow-form-v1.schema.json";
import type { WorkflowFormResponse } from "@/types/workflow-catalog.types";

// ---------------------------------------------------------------------------
// Envelope validation (wire contract v1)
//
// Order matters: an unsupported `ui_schema_version` or an unknown `requires` entry is a
// newer server talking to this UI, so it is reported as "needs a newer UI" before the
// envelope is validated against the wire schema (which would call it malformed).
// ---------------------------------------------------------------------------

export const SUPPORTED_UI_SCHEMA_VERSION = 1;

/** Capabilities this UI implements (the byte-copied v1 manifest). */
export const SUPPORTED_CAPABILITIES: ReadonlySet<string> = new Set(
  capabilityManifest.capabilities
);

export type WorkflowFormParseResult =
  | { ok: true; form: WorkflowFormResponse }
  | { ok: false; kind: "malformed"; issues: string[] }
  | { ok: false; kind: "unsupported"; version?: number; capabilities: string[] };

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const unescapePointerToken = (token: string): string =>
  token.replaceAll("~1", "/").replaceAll("~0", "~");

const formatIssues = (errors: ErrorObject[] | null | undefined): string[] => [
  ...new Set(
    (errors ?? [])
      // `if` failures only say which branch applied; the branch's own errors follow.
      .filter((error) => error.keyword !== "if")
      .map((error) => {
        const path = error.instancePath
          .split("/")
          .slice(1)
          .map(unescapePointerToken)
          .join(".");
        const missing =
          error.keyword === "required" && typeof error.params.missingProperty === "string"
            ? `${path ? "." : ""}${error.params.missingProperty}`
            : "";
        return `${path}${missing || ""}${path || missing ? ": " : ""}${error.message ?? "invalid value"}`;
      })
  ),
];

const validateEnvelope = new Ajv2020({ allErrors: true, strict: false }).compile(wireSchema);

/**
 * Validate a `/form` payload: version, then capabilities, then the wire schema.
 * Never yields a best-effort form.
 */
export const parseWorkflowFormResponse = (
  payload: unknown,
  supported: ReadonlySet<string> = SUPPORTED_CAPABILITIES
): WorkflowFormParseResult => {
  if (!isPlainObject(payload)) {
    return { ok: false, kind: "malformed", issues: ["Expected an object"] };
  }
  const version = payload.ui_schema_version;
  if (typeof version === "number" && version !== SUPPORTED_UI_SCHEMA_VERSION) {
    return { ok: false, kind: "unsupported", version, capabilities: [] };
  }
  if (Array.isArray(payload.requires)) {
    const unsupported = payload.requires.filter(
      (capability): capability is string =>
        typeof capability === "string" && !supported.has(capability)
    );
    if (unsupported.length > 0) {
      return { ok: false, kind: "unsupported", capabilities: [...new Set(unsupported)] };
    }
  }
  if (!validateEnvelope(payload)) {
    return { ok: false, kind: "malformed", issues: formatIssues(validateEnvelope.errors) };
  }
  return { ok: true, form: payload as unknown as WorkflowFormResponse };
};

// ---------------------------------------------------------------------------
// Fetching
// ---------------------------------------------------------------------------

export type WorkflowFormResult =
  | { kind: "ok"; form: WorkflowFormResponse }
  | { kind: "not_found"; message: string }
  | { kind: "unauthorized"; status: 401 | 403; message: string }
  | { kind: "http_error"; status: number; message: string }
  | { kind: "network_error"; message: string }
  | { kind: "malformed"; message: string; issues: string[] }
  | { kind: "unsupported"; message: string }
  /** A plugin's form failed registration (HTTP 503); retrying cannot repair it. */
  | { kind: "unavailable"; message: string; diagnostic: string };

export type WorkflowFormResultKind = WorkflowFormResult["kind"];

/** Build the form URL; the workflow name is always encoded as a single path segment. */
export const buildWorkflowFormUrl = (apiURL: string, name: string): string =>
  sanitizeUrl(`${apiURL}/v1/workflow/${encodeURIComponent(name)}/form`);

const notFound = (name: string): WorkflowFormResult => ({
  kind: "not_found",
  message: `Workflow "${name}" was not found or is not available through the API.`,
});

const FORM_UNAVAILABLE_CODE = "workflow_form_unavailable";

const unavailable = (detail: unknown): WorkflowFormResult | undefined => {
  if (!isPlainObject(detail) || detail.code !== FORM_UNAVAILABLE_CODE) return undefined;
  const text = (value: unknown) => (typeof value === "string" ? value : "");
  const plugin = text(detail.plugin);
  return {
    kind: "unavailable",
    message:
      `The form of this workflow${plugin ? ` from plugin "${plugin}"` : ""} failed ` +
      "validation when the workflow API started, so it cannot be shown. Ask the plugin's " +
      "author to fix it. You can still start the workflow through the API or CLI.",
    diagnostic: text(detail.message),
  };
};

const classifyFetchError = (name: string, error: unknown): WorkflowFormResult => {
  if (error instanceof TokenError) {
    return { kind: "unauthorized", status: 401, message: error.message };
  }
  if (error instanceof APIError) {
    if (error.status === 404) return notFound(name);
    if (error.status === 401 || error.status === 403) {
      return {
        kind: "unauthorized",
        status: error.status,
        message:
          error.status === 401
            ? "Your session is not authenticated. Sign in again to load this form."
            : "You do not have permission to load this form.",
      };
    }
    if (error.status === 503) {
      const result = unavailable(error.detail);
      if (result) return result;
    }
    return {
      kind: "http_error",
      status: error.status,
      message: `The workflow API returned HTTP ${error.status} while loading the form.`,
    };
  }
  // fetcher() only reaches response.json() on a successful status, so a parse failure
  // means the server answered 2xx with a body that is not JSON.
  if (error instanceof SyntaxError) {
    return {
      kind: "malformed",
      message: "The workflow API returned a form that is not valid JSON.",
      issues: [error.message],
    };
  }
  return {
    kind: "network_error",
    message: "The workflow API could not be reached. Check your connection and retry.",
  };
};

const NEWER_UI =
  "Upgrade the Config Manager UI to run this workflow from the browser. You can still " +
  "start it through the API or CLI.";

/**
 * Load and validate `GET /v1/workflow/{name}/form`. Never throws: every failure is a
 * distinct result kind so pages can render an actionable state.
 */
export const fetchWorkflowForm = async (
  apiURL: string,
  name: string
): Promise<WorkflowFormResult> => {
  // "", "." and ".." would collapse or traverse the path instead of naming a workflow.
  if (name === "" || name === "." || name === "..") return notFound(name);

  let payload: unknown;
  try {
    payload = await fetcher(buildWorkflowFormUrl(apiURL, name));
  } catch (error) {
    return classifyFetchError(name, error);
  }

  const parsed = parseWorkflowFormResponse(payload);
  if (parsed.ok) return { kind: "ok", form: parsed.form };
  if (parsed.kind === "unsupported") {
    return {
      kind: "unsupported",
      message:
        parsed.version !== undefined
          ? `This form uses UI schema version ${parsed.version}, but this UI supports ` +
            `version ${SUPPORTED_UI_SCHEMA_VERSION}. ${NEWER_UI}`
          : `This form needs ${parsed.capabilities.length === 1 ? "a capability" : "capabilities"} ` +
            `this UI does not support (${parsed.capabilities.join(", ")}). ${NEWER_UI}`,
    };
  }
  return {
    kind: "malformed",
    message: "The workflow API returned a form this UI cannot read.",
    issues: parsed.issues,
  };
};
