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
  check_count: number;
  restarts: number;
  error?: string | null;
}

export interface AppInfo {
  version: string;
  platform: string;
  plugins_dir: string;
}

export type DiagnosisEvent =
  | {
      type: "RUN_STARTED";
      run_id: string;
      device_id: string;
      mode: DiagnosticMode;
      planned: CheckDefinition[];
    }
  | { type: "CHECK_STARTED"; run_id: string; check_id: string }
  | { type: "CHECK_COMPLETED"; run_id: string; result: CheckResult }
  | { type: "RUN_COMPLETED"; run_id: string; health: HealthState; finished_at: string };
