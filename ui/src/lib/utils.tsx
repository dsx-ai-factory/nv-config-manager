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
import * as React from "react";
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { badgeVariants, BadgeProps } from "@/components/ui/badge";
import { StateHistory, Workflow, WorkflowStage } from "@/types/data-table.types";
import { Link } from "@/components/ui/link";
import { Option } from "@/types/workflow-form.types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: string | Date): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  return d.toLocaleString();
}

export function formatRelativeTime(date: string | Date): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  
  if (days > 0) return `${days}d ago`;
  if (hours > 0) return `${hours}h ago`;
  if (minutes > 0) return `${minutes}m ago`;
  return `${seconds}s ago`;
}

export function getInitialStage(stages: WorkflowStage[]): WorkflowStage | null {
  // Return null if no stages
  if (stages.length === 0) return null;

  // Prioritize: IN_PROGRESS > FAILED > last COMPLETE > first stage
  const inProgress = stages.find((stage) => stage.state === "IN_PROGRESS");
  const failed = stages.find((stage) => stage.state === "FAILED");

  const completeStages = stages.filter((stage) => stage.state === "COMPLETE");
  const lastCompleted =
    completeStages.length > 0
      ? completeStages.at(-1)
      : null;

  return inProgress ?? failed ?? lastCompleted ?? stages[0];
}

const stateToVariant: Record<StateHistory["state"], BadgeProps["variant"]> = {
  NOT_STARTED: "NOT_STARTED",
  IN_PROGRESS: "IN_PROGRESS",
  PENDING_APPROVAL: "PENDING_APPROVAL",
  COMPLETE: "COMPLETE",
  UNREACHABLE: "UNREACHABLE",
  FAILED: "FAILED",
  REJECTED: "REJECTED",
  APPROVED: "APPROVED",
};

export const handleBadgeClassName = (state: StateHistory["state"]): string => {
  return badgeVariants({ variant: stateToVariant[state] ?? "default" });
};

/**
 * Build a link to a device in the configured DCIM provider.
 *
 * @param deviceName - Device name.
 * @param deviceID - Device identifier assigned by the DCIM provider.
 * @param dcimURL - DCIM provider URL from runtime config.
 * @param dcimDisplayName - User-facing name of the DCIM provider.
 * @returns A Link component if the DCIM URL is truthy, device name if not.
 *
 */
export const buildDCIMDeviceLink = (
  deviceName: string,
  deviceID: string,
  dcimURL?: string,
  dcimDisplayName = "DCIM"
): React.ReactElement<typeof Link> | string => {
  if (dcimURL) {
    const href = `${dcimURL}/dcim/devices/${deviceID}/`;
    const sanitizedUrl = sanitizeUrl(href);
    return (
      <Link
        href={sanitizedUrl}
        title={`View device in ${dcimDisplayName} (opens in new tab)`}
      >
        {deviceName}
      </Link>
    );
  }
  return deviceName;
};

/**
 * Render a device name field based on available info.
 *
 * @param workflow Workflow record to parse device info from.
 * @param dcimURL - DCIM provider URL from runtime config.
 * @param dcimDisplayName - User-facing name of the DCIM provider.
 * @returns One of the following:
 *  - If dcimURL is set, and a device name/ID is
 *    present in the workflow data, returns a Link element pointing to the device
 *    in the DCIM provider.
 *  - If the DCIM URL is not configured, but a device record is
 *    present in the workflow, returns the plaintext device name.
 *  - If no device record is present in workflow, returns null.
 */
export const renderDeviceNameField = (
  workflow: Workflow,
  dcimURL?: string,
  dcimDisplayName = "DCIM"
): React.ReactElement<typeof Link> | string | null => {
  let deviceName: string | null = null;
  if (workflow?.search_attributes?.DeviceName) {
    deviceName = String(workflow?.search_attributes?.DeviceName[0]);
  }

  let deviceId: string | null = null;
  if (workflow?.search_attributes?.DeviceID) {
    deviceId = String(workflow?.search_attributes?.DeviceID[0]);
  }

  if (deviceName) {
    if (deviceId) {
      return buildDCIMDeviceLink(deviceName, deviceId, dcimURL, dcimDisplayName);
    } else {
      return deviceName;
    }
  } else {
    return null;
  }
};

export const startWorkflow = async (endpoint: string, params: object) => {
  // Fetch runtime config if not already available
  const configResponse = await fetch('/api/config');
  const config = await configResponse.json();
  const apiURL = config.workflowApiUrl;
  
  if (!apiURL) {
    throw new Error("API URL not configured");
  }
  
  const sanitizedUrl = sanitizeUrl(apiURL + endpoint);
  return fetch(sanitizedUrl, {
    credentials: "include",
    redirect: "error",
    mode: "cors",
    method: "post",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  }).then(async (response) => {
    if (!response.ok) {
      const result = await response.json();
      const message = result.error ?? result.detail;
      if (message) {
        throw new Error(String(message));
      } else {
        throw new Error("Failed to submit workflow.");
      }
    }
    const result = await response.json();
    globalThis.location.href = `/workflows/${result.id}`;
  });
};

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/* Remove any trailing slashes in a url.
 * */
export function sanitizeUrl(url: string): string {
  if (!url) return "";
  const res = url.replaceAll(/([^:]\/)\/+/g, "$1");
  return res;
}

/* Format object to be key value pairs to work with component design.
 * */
export const mapRoles = (
  data: Record<string, string>[] | undefined,
  keyField: string,
  valueField: string
) => {
  // Return empty array if data is undefined or not an array
  if (!Array.isArray(data)) {
    return [];
  }

  return data.map((item) => ({
    key: item[keyField],
    value: item[valueField],
  }));
};

/**
 * Converts a comma-separated string into an array of Option objects
 * @param envVarString - Comma-separated string from environment variable
 * @returns Array of Option objects with key and value properties
 */
export const envVarToOptions = (envVarString?: string): Option[] => {
  if (!envVarString) return [];

  return envVarString.split(",").map((item) => ({
    key: item.trim(),
    value: item.trim(),
  }));
};

/**
 * Formats a JSON string with proper indentation
 * @param jsonString - The JSON string to format
 * @param indentSize - Number of spaces for indentation (default: 2)
 * @returns Object with success status and either formatted JSON or error message
 */
export const formatJSON = (
  jsonString: string,
  indentSize: number = 2
): {
  success: boolean;
  result: string;
  error?: string;
} => {
  if (!jsonString.trim()) {
    return {
      success: false,
      result: jsonString,
      error: "Empty JSON string",
    };
  }

  try {
    const parsed = JSON.parse(jsonString);
    const formatted = JSON.stringify(parsed, null, indentSize);
    return {
      success: true,
      result: formatted,
    };
  } catch (error) {
    return {
      success: false,
      result: jsonString,
      error: error instanceof Error ? error.message : "Invalid JSON syntax",
    };
  }
};

/**
 * Formats a timestamp to show both local time and UTC for global users
 * @param timestamp - ISO 8601 timestamp string (e.g., "2025-07-03T22:12:19.005209Z")
 * @returns Formatted string showing local time first, then UTC in parentheses
 * @example "Jul 3, 2025, 3:12:19 PM PST (22:12:19 UTC)"
 */
export const formatTimestamp = (timestamp: string): string => {
  try {
    const date = new Date(timestamp);

    // Format local time with timezone
    const localTime = date.toLocaleString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    });

    // Format UTC time
    const utcTime = date.toLocaleString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: "UTC",
    });

    return `${localTime} (${utcTime} UTC)`;
  } catch (error) {
    console.error(error);
    return timestamp;
  }
};
