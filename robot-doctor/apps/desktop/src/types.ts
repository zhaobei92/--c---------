// TypeScript mirrors of the doctor-domain wire types (serde JSON shapes).

export type HealthState = "HEALTHY" | "DEGRADED" | "CRITICAL" | "OFFLINE" | "UNKNOWN";

export type CheckStatus =
  | "PASSED"
  | "FAILED"
  | "UNAVAILABLE"
  | "UNSUPPORTED"
  | "TIMEOUT"
  | "PERMISSION_DENIED"
  | "DEPENDENCY_MISSING"
  | "OFFLINE"
  | "CANCELLED"
  | "ERROR";

export type Severity = "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export type DiagnosticMode = "QUICK" | "FULL" | "WATCH";

export interface Capability {
  name: string;
  available: boolean;
}

export interface Device {
  id: string;
  name: string;
  endpoint: { type: "LOCAL" } | { type: "REMOTE"; host: string; port: number };
  profile_id?: string;
  connection: "CONNECTED" | "DEGRADED" | "DISCONNECTED" | "INCOMPATIBLE";
  health: HealthState;
  added_at: string;
  last_seen?: string;
  capabilities: Capability[];
}

export interface Evidence {
  id: string;
  kind: "API" | "OS" | "ROS" | "COMMAND" | "PROCESS" | "LOG" | "NETWORK" | "METRIC";
  source: string;
  summary: string;
  data: unknown;
  captured_at: string;
}

export type ObservationValue =
  | { type: "NUMBER"; value: number }
  | { type: "TEXT"; value: string }
  | { type: "BOOL"; value: boolean }
  | { type: "JSON"; value: unknown };

export interface Observation {
  id: string;
  check_id: string;
  key: string;
  value: ObservationValue;
  unit?: string;
  evidence_ids: string[];
  observed_at: string;
}

export interface Finding {
  id: string;
  device_id: string;
  check_id: string;
  rule_id?: string;
  severity: Severity;
  code: string;
  title: string;
  detail: string;
  subject: string;
  evidence_ids: string[];
  detected_at: string;
}

export interface CheckError {
  status: CheckStatus;
  message: string;
}

export interface CheckResult {
  check_id: string;
  plugin_id: string;
  device_id: string;
  status: CheckStatus;
  started_at: string;
  duration_ms: number;
  observations: Observation[];
  evidence: Evidence[];
  findings: Finding[];
  error?: CheckError;
}

export interface CheckDefinition {
  id: string;
  plugin_id: string;
  name: string;
  description: string;
  cost: "FAST" | "MEDIUM" | "SLOW";
  timeout_ms: number;
  platforms: string[];
  depends_on: string[];
  modes: DiagnosticMode[];
}

export interface CheckRun {
  id: string;
  device_id: string;
  mode: DiagnosticMode;
  started_at: string;
  finished_at?: string;
  results: CheckResult[];
}

export interface PluginSummary {
  id: string;
  name: string;
  version: string;
  api_version: number;
  description: string;
  /** Runtime-discovered capability names (empty until first contact). */
  capabilities: string[];
  /** Runtime-discovered check ids (empty until first contact). */
  checks: string[];
  max_concurrency: number;
  restarts: number;
  contacted: boolean;
  error?: string | null;
}

export interface AppInfo {
  version: string;
  platform: string;
  plugins_dir: string;
  database_path?: string | null;
  storage_ok: boolean;
}

export interface NetworkTarget {
  host: string;
  port?: number;
  timeout_ms?: number;
}

export interface RunFilter {
  device_id?: string;
  mode?: DiagnosticMode;
  health?: HealthState;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface RunSummaryRow {
  id: string;
  device_id: string;
  device_name: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at?: string | null;
  overall_health?: string | null;
  app_version: string;
  duration_ms?: number | null;
  check_count: number;
  finding_count: number;
}

export interface PluginSnapshot {
  plugin_id: string;
  version: string;
  api_version: number;
  capabilities: string[];
}

export interface StoredRun {
  run: CheckRun;
  status: string;
  overall_health?: HealthState | null;
  app_version: string;
  plugin_snapshots: PluginSnapshot[];
}

export type DiagnosisEvent =
  | {
      type: "RUN_STARTED";
      run_id: string;
      device_id: string;
      mode: DiagnosticMode;
      planned: CheckDefinition[];
    }
  | { type: "PLUGIN_STARTED"; run_id: string; plugin_id: string }
  | { type: "CHECK_QUEUED"; run_id: string; check_id: string }
  | { type: "CHECK_STARTED"; run_id: string; check_id: string }
  | { type: "CHECK_COMPLETED"; run_id: string; result: CheckResult }
  | {
      type: "CHECK_SKIPPED";
      run_id: string;
      check_id: string;
      reason: string;
      prerequisite?: string | null;
      result: CheckResult;
    }
  | { type: "PLUGIN_COMPLETED"; run_id: string; plugin_id: string }
  | {
      type: "RUN_COMPLETED";
      run_id: string;
      health: HealthState;
      finished_at: string;
      persisted: boolean;
    };
