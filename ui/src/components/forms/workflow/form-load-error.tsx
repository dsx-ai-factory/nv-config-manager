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

/** Launcher states shown instead of a workflow form. */
import Link from "next/link";
import * as React from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { WorkflowFormResult } from "@/lib/workflow-form";

export type FormLoadError = Exclude<WorkflowFormResult, { kind: "ok" } | { kind: "not_found" }>;

const ERROR_TITLES: Record<FormLoadError["kind"], string> = {
  unauthorized: "The form could not be loaded",
  http_error: "The form could not be loaded",
  network_error: "The workflow API could not be reached",
  malformed: "The form is invalid",
  unsupported: "This form needs a newer UI",
  unavailable: "This form is unavailable",
};

const API_OR_CLI = "You can still start the workflow through the API or CLI.";

/** What the user can do about each error, after the loader's own message. */
const errorHint = (error: FormLoadError): string | null => {
  switch (error.kind) {
    case "unauthorized":
      return error.status === 403 ? "Ask an administrator for access to this workflow." : null;
    case "http_error":
      return "Try again. If it keeps failing, check the workflow API logs.";
    case "malformed":
      return `Report this to the workflow's author. ${API_OR_CLI}`;
    default:
      return null;
  }
};

/** Shared card chrome for workflow-form states that show no form. */
export const PageCard = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="flex items-center justify-center p-6">
    <Card className="w-full max-w-3xl border-2 shadow-md">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  </div>
);

export const ReturnToWorkflows = () => (
  <Button asChild variant="outline">
    <Link href="/workflows">Return to Workflows</Link>
  </Button>
);

/**
 * A form that could not be loaded. Only transient failures offer **Try again**; an
 * unavailable plugin form cannot be repaired by retrying.
 */
export const FormUnavailable = ({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: FormLoadError;
  onRetry: () => void;
}) => {
  const hint = errorHint(error);
  const retry =
    error.kind === "http_error" || error.kind === "network_error" ? (
      <Button onClick={onRetry}>Try again</Button>
    ) : error.kind === "unauthorized" && error.status === 401 ? (
      // Reloading the page renews the SSO session.
      <Button onClick={() => globalThis.location.reload()}>Reload page</Button>
    ) : null;

  return (
    <PageCard title={title}>
      <Alert variant="destructive">
        <AlertTitle>{ERROR_TITLES[error.kind]}</AlertTitle>
        <AlertDescription>
          <p>{error.message}</p>
          {hint ? <p>{hint}</p> : null}
          {error.kind === "unavailable" && error.diagnostic ? (
            <pre className="mt-2 whitespace-pre-wrap break-words text-xs">{error.diagnostic}</pre>
          ) : null}
          {error.kind === "malformed" && error.issues.length > 0 ? (
            <ul className="mt-2 list-disc pl-5">
              {error.issues.map((issue, index) => (
                <li key={index}>{issue}</li>
              ))}
            </ul>
          ) : null}
        </AlertDescription>
      </Alert>
      <div className="flex gap-4">
        {retry}
        <ReturnToWorkflows />
      </div>
    </PageCard>
  );
};
