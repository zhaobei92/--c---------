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

// ── ROS observation types (wire mirrors of doctor-domain::ros) ──

export interface RosRuntimeConfig {
  id: string;
  name: string;
  mode: "AUTO" | "CONFIGURED" | "INHERITED" | "FIXTURE";
  setup_scripts: string[];
  domain_id?: number | null;
  rmw?: string | null;
  extra_env?: Record<string, string>;
  fixture?: string | null;
  preferred_provider?: string | null;
}

export interface RosEndpointInfo {
  node: string;
  endpoint_type: "PUBLISHER" | "SUBSCRIPTION";
  topic_type: string;
  gid?: string | null;
  qos: Record<string, unknown>;
}

export interface RosNodeInfo {
  name: string;
  namespace: string;
  full_name: string;
  publishers: string[];
  subscriptions: string[];
  services: string[];
  actions: string[];
}

export interface RosTopicInfo {
  name: string;
  types: string[];
  publisher_count: number;
  subscriber_count: number;
  publishers: RosEndpointInfo[];
  subscribers: RosEndpointInfo[];
}

export interface RosGraphSnapshot {
  runtime_id: string;
  provider: string;
  captured_at: string;
  discovery_ms: number;
  nodes: RosNodeInfo[];
  topics: RosTopicInfo[];
  services: { name: string; types: string[]; providers: string[] }[];
  actions: { name: string; types: string[]; servers: string[] }[];
}

export interface RosTfSnapshot {
  frames: string[];
  edges: {
    parent: string;
    child: string;
    is_static?: boolean | null;
    last_stamp?: number | null;
  }[];
  connected_components: string[][];
  listen_ms: number;
}

export interface RosDiagnosticStatus {
  name: string;
  hardware_id: string;
  level: number;
  message: string;
  values: Record<string, string>;
  stamp?: number | null;
  source_topic: string;
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
