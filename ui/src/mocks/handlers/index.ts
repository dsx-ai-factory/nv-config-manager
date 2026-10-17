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
import { healthCheckHandlers } from "./healthcheckHandlers";
import { workflowFetchingHandlers } from "./workflowHandlers";
import { spxOverlayHandlers } from "./spxOverlayHandlers";
import { configBackupHandlers } from "./backupHandlers";
import { useEnvDataHandlers } from "./useEnvHandlers";
import { useDevicesHandlers } from "./useDevicesHandlers";
import { portLLDPInfoHandlers } from "./portlldpinfoHandlers";
import { siteCableValidationHandlers } from "./siteCableValidationHandlers";
import { siteBackupHandlers } from "./siteBackupHandlers";
import { deviceCableValidationHandlers } from "./deviceCableValidationHandlers";
import { devicePasswordRotationHandlers } from "./devicePasswordRotationHandlers";
import { connectedHostMetadataHandlers } from "./connectedHostMetadataHandlers";
import { deployHandlers } from "./deployHandlers";
import { ibValidationHandlers } from "./ibValidationHandlers";
import { ibOsUpgradeHandlers } from "./ibOsUpgradeHandler";
import { reprovisionHandlers } from "./reprovisionHandlers";
import { switchOsUpgradeHandlers } from "./switchOsUpgradeHandlers";
import { cumulusHardwareValidationHandlers } from "./cumulusHardwareValidationHandler";
import { multiDeployHandlers } from "./multiDeployHandlers";
import { ibPkeyCreationHandlers } from "./ibPkeyCreationHandlers";
import { workflowFormOptionHandlers } from "./workflowFormOptionHandlers";

export * from "./healthcheckHandlers";
export * from "./workflowHandlers";
export * from "./spxOverlayHandlers";
export * from "./backupHandlers";
export * from "./useEnvHandlers";
export * from "./useDevicesHandlers";
export * from "./portlldpinfoHandlers";
export * from "./siteCableValidationHandlers";
export * from "./siteBackupHandlers";
export * from "./deviceCableValidationHandlers";
export * from "./devicePasswordRotationHandlers";
export * from "./connectedHostMetadataHandlers";
export * from "./deployHandlers";
export * from "./ibValidationHandlers";
export * from "./ibOsUpgradeHandler";
export * from "./reprovisionHandlers";
export * from "./switchOsUpgradeHandlers";
export * from "./cumulusHardwareValidationHandler";
export * from "./multiDeployHandlers";
export * from "./ibPkeyCreationHandlers";
export * from "./workflowFormOptionHandlers";

export const handlers = [
  ...healthCheckHandlers,
  ...workflowFetchingHandlers,
  ...spxOverlayHandlers,
  ...configBackupHandlers,
  ...useEnvDataHandlers,
  ...useDevicesHandlers,
  ...portLLDPInfoHandlers,
  ...siteCableValidationHandlers,
  ...siteBackupHandlers,
  ...deviceCableValidationHandlers,
  ...devicePasswordRotationHandlers,
  ...connectedHostMetadataHandlers,
  ...deployHandlers,
  ...ibValidationHandlers,
  ...ibOsUpgradeHandlers,
  ...reprovisionHandlers,
  ...switchOsUpgradeHandlers,
  ...cumulusHardwareValidationHandlers,
  ...multiDeployHandlers,
  ...ibPkeyCreationHandlers,
  ...workflowFormOptionHandlers,
];
