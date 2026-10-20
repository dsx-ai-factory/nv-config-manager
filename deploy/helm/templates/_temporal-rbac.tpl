{{/*
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
*/}}
{{- define "nv-config-manager.temporalRbac" -}}
{{- $rbac := deepCopy .Values.rbac -}}
{{- if hasKey $rbac "workflowOverrides" -}}
  {{- $overrides := $rbac.workflowOverrides -}}
  {{- if not (kindIs "map" $overrides) -}}
    {{- fail "rbac.workflowOverrides must be a mapping keyed by workflow name" -}}
  {{- end -}}
  {{- $workflows := dict -}}
  {{- $names := list -}}
  {{- $base := $rbac.workflows | default list -}}
  {{- if not (kindIs "slice" $base) -}}
    {{- fail "rbac.workflows must be a list" -}}
  {{- end -}}
  {{- range $workflow := $base -}}
    {{- if not (kindIs "map" $workflow) -}}
      {{- fail "rbac.workflows entries must be mappings with a name" -}}
    {{- end -}}
    {{- $name := $workflow.name -}}
    {{- if or (not (kindIs "string" $name)) (empty $name) -}}
      {{- fail "rbac.workflows entries must have a non-empty string name" -}}
    {{- end -}}
    {{- if hasKey $workflows $name -}}
      {{- fail (printf "rbac.workflows contains duplicate workflow %q" $name) -}}
    {{- end -}}
    {{- $_ := set $workflows $name $workflow -}}
    {{- $names = append $names $name -}}
  {{- end -}}
  {{- range $name := keys $overrides | sortAlpha -}}
    {{- if empty $name -}}
      {{- fail "rbac.workflowOverrides workflow names must not be empty" -}}
    {{- end -}}
    {{- $override := index $overrides $name -}}
    {{- if not (kindIs "map" $override) -}}
      {{- fail (printf "rbac.workflowOverrides.%s must be a mapping" $name) -}}
    {{- end -}}
    {{- range $key, $value := $override -}}
      {{- if not (has $key (list "read_roles" "execute_roles" "enabled")) -}}
        {{- fail (printf "rbac.workflowOverrides.%s has unsupported field %q" $name $key) -}}
      {{- end -}}
      {{- if eq $key "enabled" -}}
        {{- if not (kindIs "bool" $value) -}}
          {{- fail (printf "rbac.workflowOverrides.%s.enabled must be a boolean" $name) -}}
        {{- end -}}
      {{- else -}}
        {{- if not (kindIs "slice" $value) -}}
          {{- fail (printf "rbac.workflowOverrides.%s.%s must be a list of strings" $name $key) -}}
        {{- end -}}
        {{- range $role := $value -}}
          {{- if not (kindIs "string" $role) -}}
            {{- fail (printf "rbac.workflowOverrides.%s.%s must be a list of strings" $name $key) -}}
          {{- end -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
    {{- if and (hasKey $override "enabled") (not $override.enabled) -}}
      {{- $_ := unset $workflows $name -}}
    {{- else -}}
      {{- if not (hasKey $workflows $name) -}}
        {{- $_ := set $workflows $name (dict "name" $name "read_roles" list "execute_roles" list) -}}
        {{- $names = append $names $name -}}
      {{- end -}}
      {{- $workflow := index $workflows $name -}}
      {{- range $key := list "read_roles" "execute_roles" -}}
        {{- if hasKey $override $key -}}
          {{- $_ := set $workflow $key (index $override $key) -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
  {{- $resolved := list -}}
  {{- range $name := $names -}}
    {{- if hasKey $workflows $name -}}
      {{- $resolved = append $resolved (index $workflows $name) -}}
    {{- end -}}
  {{- end -}}
  {{- $_ := set $rbac "workflows" $resolved -}}
  {{- $_ := unset $rbac "workflowOverrides" -}}
{{- end -}}
{{- toYaml $rbac -}}
{{- end -}}
