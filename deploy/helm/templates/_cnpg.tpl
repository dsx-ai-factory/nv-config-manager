{{/*
Names of rendered CNPG clusters that have backups enabled (space-separated).
Mirrors the render conditions in cnpg-clusters.yaml (clusterList entries plus
the special-cased Nautobot cluster). Empty when no rendered cluster backs up.
*/}}
{{- define "nv-config-manager.cnpg.backupClusters" -}}
{{- $names := list -}}
{{- if .Values.cnpg.enabled -}}
  {{- range $key := .Values.cnpg.clusterList -}}
    {{- $c := index $.Values.cnpg $key -}}
    {{- if and $c.enabled (dig "backup" "enabled" false $c) -}}
      {{- $names = append $names $c.clusterName -}}
    {{- end -}}
  {{- end -}}
  {{- $n := .Values.cnpg.nautobot -}}
  {{- if and (or $n.enabled (and .Values.nautobot.enabled .Values.externalServices.nautobot.local)) (dig "backup" "enabled" false $n) -}}
    {{- $names = append $names $n.clusterName -}}
  {{- end -}}
{{- end -}}
{{- join " " $names -}}
{{- end -}}
