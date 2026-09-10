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
import { useEffect } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/fetcher";
import { useRuntimeConfig } from "@/config/runtime";
import { Option } from "@/types/workflow-form.types";
import { mapLocationOptions } from "@/lib/location-options";
import { sanitizeUrl, mapRoles } from "@/lib/utils";

const useEnvData = () => {
  const { config: runtimeConfig } = useRuntimeConfig();
  const apiURL = runtimeConfig?.workflowApiUrl || "";

  // Always use parameter endpoints - no longer honor ALLOWED_* env vars
  const {
    data: siteData,
    error: siteError,
    isLoading: siteIsLoading,
  } = useSWR(
    runtimeConfig
      ? sanitizeUrl(
          `${apiURL}/v1/parameter/location?location_type=Site&location_type=Module`
        )
      : null,
    fetcher
  );

  const {
    data: rolesData,
    error: rolesError,
    isLoading: rolesIsLoading,
  } = useSWR(
    runtimeConfig
      ? sanitizeUrl(`${apiURL}/v1/parameter/role?managed_only=true`)
      : null,
    fetcher
  );

  const {
    data: statusesData,
    error: statusesError,
    isLoading: statusesIsLoading,
  } = useSWR(
    runtimeConfig
      ? sanitizeUrl(`${apiURL}/v1/parameter/status?content_type=dcim.device`)
      : null,
    fetcher
  );

  const {
    data: tenantsData,
    error: tenantsError,
    isLoading: tenantsIsLoading,
  } = useSWR(
    runtimeConfig
      ? sanitizeUrl(`${apiURL}/v1/parameter/tenant?managed_only=true`)
      : null,
    fetcher
  );

  useEffect(() => {
    if (siteError) console.error(siteError);
  }, [siteError]);
  useEffect(() => {
    if (rolesError) console.error(rolesError);
  }, [rolesError]);
  useEffect(() => {
    if (statusesError) console.error(statusesError);
  }, [statusesError]);
  useEffect(() => {
    if (tenantsError) console.error(tenantsError);
  }, [tenantsError]);

  const sites = siteData && !siteError ? mapLocationOptions(siteData) : [];
  const roles =
    rolesData && !rolesError ? mapRoles(rolesData, "name", "name") : [];
  const statuses =
    statusesData && !statusesError
      ? mapRoles(statusesData, "name", "name")
      : [];
  const tenants =
    tenantsData && !tenantsError
      ? mapRoles(tenantsData, "name", "name")
      : [];

  return {
    data: {
      siteData: sites,
      rolesData: roles,
      statusData: statuses,
      tenantsData: tenants,
      deviceTypeIdsData: [] as Option[],
    },
    errors: {
      siteError,
      rolesError,
      statusesError,
      tenantsError,
    },
    isLoading: {
      siteIsLoading,
      rolesIsLoading,
      statusesIsLoading,
      tenantsIsLoading,
    },
  };
};

export default useEnvData;
