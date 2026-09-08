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
 * Utilities for downloading device configurations.
 * Used for pre-generating configs in airgapped environments.
 */

/**
 * Download a single config file as a blob (triggers browser download).
 */
export function downloadConfigFile(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export interface ConfigForDownload {
  filename: string;
  content: string;
  deviceName?: string;
  deviceUuid?: string;
}

export interface DownloadZipOptions {
  /** Zip filename (default: "nv-config-manager-configs.zip") */
  filename?: string;
}

/**
 * Download multiple configs as a zip archive.
 * Structure: {deviceName}/{filename} or {filename} if no deviceName.
 */
export async function downloadConfigsAsZip(
  configs: ConfigForDownload[],
  options?: DownloadZipOptions
): Promise<void> {
  const JSZip = (await import("jszip")).default;
  const zip = new JSZip();

  for (const config of configs) {
    const folder = config.deviceName ? zip.folder(config.deviceName) : zip;
    if (folder) {
      folder.file(config.filename, config.content);
    }
  }

  const blob = await zip.generateAsync({ type: "blob" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = options?.filename ?? "nv-config-manager-configs.zip";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
