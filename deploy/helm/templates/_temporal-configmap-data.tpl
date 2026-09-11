{{/*
=============================================================================
Temporal ConfigMap Data Templates
=============================================================================
Named templates for Temporal ConfigMap data sections, used in both ConfigMap
definitions and Deployment checksum annotations. This ensures pods
automatically restart when ConfigMap content changes.

Usage in ConfigMap:
  data:
  {{- include "nv-config-manager.configmap.<name>" . | nindent 2 }}

Usage in Deployment annotation:
  checksum/<name>: {{ include "nv-config-manager.configmap.<name>" . | sha256sum }}
=============================================================================
*/}}

{{/*
Temporal RBAC ConfigMap data
*/}}
{{- define "nv-config-manager.configmap.temporal-rbac" -}}
rbac.yaml: |
{{- toYaml .Values.rbac | nindent 2 }}
{{- end -}}

{{/*
Temporal server ConfigMap data (PostgreSQL configuration)
*/}}
{{- define "nv-config-manager.configmap.temporal-config" -}}
config_template.yaml: |-
  log:
    stdout: true
    level: DEBUG

  persistence:
    numHistoryShards: {{ include "nv-config-manager.temporalShardCount" . }}
    defaultStore: default
    visibilityStore: visibility
    datastores:
      default:
        sql:
          pluginName: "postgres12"
          databaseName: "{{ .Values.externalServices.postgres.temporal.database }}"
          connectAddr: {{ `{{ default .Env.POSTGRES_HOST "127.0.0.1" }}` }}:{{ .Values.externalServices.postgres.port }}
          connectProtocol: "tcp"
          user: {{ `{{ default .Env.POSTGRES_USER "temporal" }}` }}
          password: {{ `{{ default .Env.POSTGRES_PASS "temporal" }}` }}
          maxConns: 20
          maxIdleConns: 20
          maxConnLifetime: 1h
          tls:
            enabled: false
      visibility:
        sql:
          pluginName: "postgres12"
          databaseName: "{{ .Values.externalServices.postgres.temporal.visibilityDatabase }}"
          connectProtocol: "tcp"
          connectAddr: {{ `{{ default .Env.POSTGRES_VISIBILITY_HOST (default .Env.POSTGRES_HOST "127.0.0.1") }}` }}:{{ .Values.externalServices.postgres.port }}
          user: {{ `{{ default .Env.POSTGRES_VISIBILITY_USER (default .Env.POSTGRES_USER "temporal") }}` }}
          password: {{ `{{ default .Env.POSTGRES_VISIBILITY_PASS (default .Env.POSTGRES_PASS "temporal") }}` }}
          maxConns: 10
          maxIdleConns: 10
          maxConnLifetime: 1h
          tls:
            enabled: false

  global:
    membership:
      name: temporal
      maxJoinDuration: 30s
      broadcastAddress: {{ `{{ default .Env.POD_IP "0.0.0.0" }}` }}

    pprof:
      port: 7936

    metrics:
      tags:
        type: {{ `{{ .Env.SERVICES }}` }}
      prometheus:
        timerType: histogram
        listenAddress: "0.0.0.0:8000"

  services:
    frontend:
      rpc:
        grpcPort: {{ .Values.temporal.services.frontend.port }}
        membershipPort: {{ .Values.temporal.services.frontend.membershipPort }}
        bindOnIP: "0.0.0.0"

    history:
      rpc:
        grpcPort: {{ .Values.temporal.services.history.port }}
        membershipPort: {{ .Values.temporal.services.history.membershipPort }}
        bindOnIP: "0.0.0.0"

    matching:
      rpc:
        grpcPort: {{ .Values.temporal.services.matching.port }}
        membershipPort: {{ .Values.temporal.services.matching.membershipPort }}
        bindOnIP: "0.0.0.0"

    worker:
      rpc:
        grpcPort: {{ .Values.temporal.services.worker.port }}
        membershipPort: {{ .Values.temporal.services.worker.membershipPort }}
        bindOnIP: "0.0.0.0"

  clusterMetadata:
    enableGlobalDomain: false
    failoverVersionIncrement: 10
    masterClusterName: "active"
    currentClusterName: "active"
    clusterInformation:
      active:
        enabled: true
        initialFailoverVersion: 1
        rpcName: "temporal-frontend"
        rpcAddress: "127.0.0.1:{{ .Values.temporal.services.frontend.port }}"

  dcRedirectionPolicy:
      policy: "noop"

  archival:
    status: "disabled"

  dynamicConfigClient:
    filepath: "/etc/temporal/dynamic_config/dynamic_config.yaml"
    pollInterval: "10s"
{{- end -}}

{{/*
Temporal Dynamic Config ConfigMap data
*/}}
{{- define "nv-config-manager.configmap.temporal-dynamic-config" -}}
dynamic_config.yaml: |-
  # Dynamic configuration for Temporal
  # See: https://docs.temporal.io/references/dynamic-configuration
  {{- if .Values.temporal.dynamicConfig }}
  {{- range .Values.temporal.dynamicConfig }}
  - key: {{ .key }}
    value: {{ .value }}
    {{- if .constraints }}
    constraints: {{ toYaml .constraints | nindent 6 }}
    {{- end }}
  {{- end }}
  {{- end }}
{{- end -}}
