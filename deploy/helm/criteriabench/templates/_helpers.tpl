{{- define "criteriabench.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "criteriabench.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "criteriabench.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "criteriabench.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: criteriabench
{{- end }}

{{- define "criteriabench.selectorLabels" -}}
app.kubernetes.io/name: {{ include "criteriabench.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "criteriabench.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "criteriabench.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "criteriabench.databaseSecretName" -}}
{{- if .Values.demoDependencies.enabled }}
{{- printf "%s-demo-database" (include "criteriabench.fullname" .) }}
{{- else }}
{{- required "database.existingSecret is required when demoDependencies.enabled=false" .Values.database.existingSecret }}
{{- end }}
{{- end }}

{{- define "criteriabench.redisUrl" -}}
{{- default (printf "redis://%s-redis:6379/0" (include "criteriabench.fullname" .)) .Values.config.redisUrl -}}
{{- end }}

{{- define "criteriabench.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end }}
