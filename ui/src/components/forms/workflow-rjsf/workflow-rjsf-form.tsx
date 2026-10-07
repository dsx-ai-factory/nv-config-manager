"use client";
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
 * The schema-driven workflow form: a thin, controlled RJSF shell.
 *
 * The shell owns the projected form data, device filter scopes, pending URL prefill
 * owners, and mapped server errors (see `state.ts`); core fields read and write them
 * through the form context. RJSF renders `formData={state.formData}` and its changes
 * are merged back three-way, so a field patch applied since the render that produced
 * an RJSF change is never rolled back.
 */
import * as React from "react";
import Form, { type IChangeEvent } from "@rjsf/core";
import { getSubmitButtonOptions, type RJSFSchema, type UiSchema } from "@rjsf/utils";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { getErrorMessage, startWorkflow } from "@/lib/utils";
import type { WorkflowCatalogEntry, WorkflowFormResponse } from "@/types/workflow-catalog.types";

import { useShellState } from "./context";
import { buildPayload } from "./payload";
import { snapshotQuery, type SearchParamsLike } from "./prefill";
import { mapServerErrors } from "./server-errors";
import { RJSF_DEFAULT_STATE_BEHAVIOR, type FormData } from "./state";
import { workflowTheme } from "./theme";
import { createCustomValidate, createTransformErrors, workflowValidator } from "./validator";

export interface WorkflowRjsfFormProps {
  /** Normalised catalog entry: title and submit endpoint. */
  entry: WorkflowCatalogEntry;
  /** Validated `/v1/workflow/{name}/form` response (see `useWorkflowForm`). */
  form: WorkflowFormResponse;
  /** Query parameters for prefill, e.g. `useSearchParams()`. Read once, on mount. */
  searchParams: SearchParamsLike | null;
}

const statusOf = (error: unknown): number | undefined => {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : undefined;
};

const RjsfForm = ({ entry, form, searchParams }: WorkflowRjsfFormProps) => {
  const { toast } = useToast();
  const schema = form.schema as RJSFSchema;
  const uiSchema = form.ui_schema as UiSchema;
  const [query] = React.useState(() => snapshotQuery(searchParams));
  const { state, dispatch, context } = useShellState(schema, uiSchema, query);
  const [submitAttempted, setSubmitAttempted] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  const transformErrors = React.useMemo(
    () => createTransformErrors(schema, uiSchema, context.layout),
    [schema, uiSchema, context.layout]
  );
  const customValidate = React.useMemo(() => createCustomValidate(schema, uiSchema), [schema, uiSchema]);
  const { submitText = "Submit" } = getSubmitButtonOptions(uiSchema);
  const submitPath = `/v1/workflow${entry.endpoint}`;

  // This render's form data is the merge base for the changes RJSF reports from it.
  const base = state.formData;
  const onChange = (event: IChangeEvent) =>
    dispatch({ type: "rjsf-change", base, next: (event.formData ?? {}) as FormData });

  const submit = async () => {
    // The payload comes from the shell state, never from an RJSF event snapshot.
    const payload = buildPayload(schema, uiSchema, state.formData);
    setSubmitting(true);
    try {
      await startWorkflow(submitPath, payload); // navigates to the new run
      dispatch({ type: "server-errors", errors: undefined });
    } catch (error) {
      const mapped =
        statusOf(error) === 422
          ? mapServerErrors(schema, uiSchema, context.layout, (error as { detail?: unknown }).detail)
          : null;
      if (mapped) {
        dispatch({ type: "server-errors", errors: mapped });
      } else {
        toast({ variant: "destructive", title: "Workflow Failed", description: getErrorMessage(error) });
      }
      setSubmitting(false);
    }
  };

  return (
    <Form
      schema={schema}
      uiSchema={uiSchema}
      formData={state.formData}
      formContext={context}
      validator={workflowValidator}
      experimental_defaultFormStateBehavior={RJSF_DEFAULT_STATE_BEHAVIOR}
      {...workflowTheme}
      className="space-y-6"
      noHtml5Validate
      showErrorList={false}
      // Validate on submit; after a failed submit, re-validate on every data update.
      liveValidate={submitAttempted ? "onChange" : undefined}
      transformErrors={transformErrors}
      customValidate={customValidate}
      extraErrors={state.serverErrors}
      disabled={submitting}
      onChange={onChange}
      onError={() => setSubmitAttempted(true)}
      onSubmit={() => void submit()}
    >
      <Button type="submit" disabled={submitting || state.pending.size > 0}>
        {submitting ? "Submitting..." : submitText}
      </Button>
    </Form>
  );
};

/** Card chrome shared with the launcher's other states. */
export const WorkflowRjsfForm = (props: WorkflowRjsfFormProps) => (
  <div className="flex items-center justify-center p-6">
    <Card className="w-full max-w-3xl border-2 shadow-md">
      <CardHeader>
        <CardTitle>{`New ${props.entry.display_name} Workflow`}</CardTitle>
      </CardHeader>
      <CardContent>
        {/* A different workflow starts from fresh state. */}
        <RjsfForm key={props.entry.name} {...props} />
      </CardContent>
    </Card>
  </div>
);
