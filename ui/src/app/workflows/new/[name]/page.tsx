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

import { useSearchParams } from "next/navigation";
import * as React from "react";

import {
  FormUnavailable,
  PageCard,
  ReturnToWorkflows,
} from "@/components/forms/workflow/form-load-error";
import { WorkflowRjsfForm } from "@/components/forms/workflow-rjsf/workflow-rjsf-form";
import { WorkflowFormSkeleton } from "@/components/loading";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import useWorkflowCatalog from "@/hooks/useWorkflowCatalog";
import useWorkflowForm from "@/hooks/useWorkflowForm";
import useWhoami from "@/hooks/useWhoami";
import { isLegacyWorkflowCatalog } from "@/lib/workflow-catalog";
import {
  getWorkflowExecutePermission,
  WORKFLOW_FORM_API_UPGRADE_REQUIRED,
} from "@/lib/workflow-launcher";

interface NewWorkflowPageProps {
  readonly params: Promise<{ name: string }>;
}

/**
 * Next.js passes the path segment still percent-encoded, and links encode the workflow
 * form ID (`workflowFormPath`). A malformed escape stays as-is and is simply not found.
 */
const decodeSegment = (segment: string): string => {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
};

const WorkflowNotFound = ({ name }: { name: string }) => (
  <PageCard title="Workflow not found">
    <p>
      Workflow &quot;{name}&quot; was not found or is not available through the
      API. Check the link, or choose a workflow from the New workflow menu.
    </p>
    <ReturnToWorkflows />
  </PageCard>
);

const CatalogUnavailable = ({ error }: { error: Error }) => (
  <PageCard title="Workflows could not be loaded">
    <Alert variant="destructive">
      <AlertTitle>The workflow catalog could not be loaded</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        <p>Reload the page to try again.</p>
      </AlertDescription>
    </Alert>
    <div className="flex gap-4">
      <Button onClick={() => globalThis.location.reload()}>Reload page</Button>
      <ReturnToWorkflows />
    </div>
  </PageCard>
);

const WorkflowApiUpgradeRequired = () => (
  <PageCard title="Workflow API upgrade required">
    <Alert variant="destructive">
      <AlertTitle>Browser workflow forms are unavailable</AlertTitle>
      <AlertDescription>
        <p>{WORKFLOW_FORM_API_UPGRADE_REQUIRED}</p>
        <p>You can still start the workflow through the API or CLI.</p>
      </AlertDescription>
    </Alert>
    <ReturnToWorkflows />
  </PageCard>
);

export default function NewWorkflowPage({ params }: NewWorkflowPageProps) {
  const formId = decodeSegment(React.use(params).name);
  const searchParams = useSearchParams();
  const {
    catalog,
    error: catalogError,
    isLoaded: catalogLoaded,
  } = useWorkflowCatalog();
  const entry = catalog.find((candidate) => candidate.form_id === formId);
  const legacyCatalog = isLegacyWorkflowCatalog(catalog);
  // Do not probe a generic-form endpoint until metadata from a compatible API explicitly
  // advertises it. Older APIs omit `has_form` and may not have the endpoint at all.
  const { result, reload } = useWorkflowForm(
    formId,
    entry?.has_form === true && entry.form_id !== null
  );
  const {
    isLoaded: whoamiLoaded,
    isRetrying: whoamiRetrying,
    isUnauthorized,
    reload: reloadWhoami,
    status: whoamiStatus,
    userRoles,
  } = useWhoami();
  const reasonId = React.useId();

  if (catalogLoaded && (legacyCatalog || entry?.has_form === null))
    return <WorkflowApiUpgradeRequired />;
  if (catalogLoaded && (!entry || entry.has_form === false)) {
    return <WorkflowNotFound name={formId} />;
  }
  if (!catalogLoaded && catalogError)
    return <CatalogUnavailable error={catalogError} />;
  if (!entry || !result || !whoamiLoaded) return <WorkflowFormSkeleton />;
  if (result.kind === "not_found") return <WorkflowNotFound name={formId} />;
  if (result.kind !== "ok") {
    return (
      <FormUnavailable
        title={`New ${entry.display_name} Workflow`}
        error={result}
        onRetry={() => void reload()}
      />
    );
  }

  // Role information improves the form UX, but workflow submission remains the
  // authorization boundary. Keep the form available when only this advisory probe fails.
  const whoamiUnavailable = whoamiStatus === "unavailable";
  const permission = whoamiUnavailable
    ? { allowed: true as const }
    : getWorkflowExecutePermission(entry, userRoles, isUnauthorized);
  return (
    <>
      {whoamiUnavailable ? (
        <div className="flex justify-center px-6 pt-6">
          <Alert className="w-full max-w-3xl" variant="destructive">
            <AlertTitle>Permissions could not be verified</AlertTitle>
            <AlertDescription>
              <p>
                The workflow form remains available. The server will verify
                your permission when you submit it.
              </p>
              <Button
                className="mt-3"
                disabled={whoamiRetrying}
                onClick={() => void reloadWhoami()}
                type="button"
                variant="outline"
              >
                {whoamiRetrying ? "Retrying..." : "Retry permission check"}
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      ) : null}
      {permission.allowed ? null : (
        <div className="flex justify-center px-6 pt-6">
          <Alert className="w-full max-w-3xl">
            <AlertTitle>You cannot start this workflow</AlertTitle>
            <AlertDescription id={reasonId}>
              {permission.reason}
            </AlertDescription>
          </Alert>
        </div>
      )}
      {/* A disabled fieldset disables every control and the submit button inside it. */}
      <fieldset
        disabled={!permission.allowed}
        aria-describedby={permission.allowed ? undefined : reasonId}
        className="min-w-0"
      >
        <WorkflowRjsfForm
          entry={entry}
          form={result.form}
          searchParams={searchParams}
        />
      </fieldset>
    </>
  );
}
