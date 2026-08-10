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
    }
  | {
      type: "EVALUATION_COMPLETED";
      run_id: string;
      profile_id: string;
      profile_revision: number;
      satisfied: number;
      unsatisfied: number;
      unknown: number;
      not_applicable: number;
    };

// ── Phase C2: comparison, baselines, profiles, evaluation ──────────────

export type AttributeValue =
  | { type: "NUMBER"; value: number }
  | { type: "TEXT"; value: string }
  | { type: "BOOL"; value: boolean }
  | { type: "TEXT_SET"; value: string[] };

export interface EntityKey {
  namespace: string;
  kind: string;
  key: string;
}

export interface ComparisonEntity {
  key: EntityKey;
  display_name: string;
  attributes: Record<string, AttributeValue>;
  source_plugin: string;
  source_check: string;
  source_evidence_ids: string[];
}

export type NamespaceAvailability = "OBSERVED" | "NOT_OBSERVED" | "UNSUPPORTED";

export type EntityStability = "STABLE" | "VARIABLE" | "TRANSIENT_CANDIDATE";

export interface NumericSummary {
  min: number;
  max: number;
  median: number;
  samples: number;
}

export interface BaselineEntity {
  key: EntityKey;
  display_name: string;
  attributes: Record<string, AttributeValue>;
  presence_count: number;
  presence_ratio: number;
  stability: EntityStability;
  numeric_summaries: Record<string, NumericSummary>;
  observed_values: Record<string, string[]>;
  source_plugin: string;
  source_check: string;
  source_evidence_ids: string[];
}

export interface BaselineSource {
  run_id: string;
  run_started_at: string;
  mode: string;
  overall_health?: string | null;
  manually_accepted: boolean;
}

export interface Baseline {
  id: string;
  device_id: string;
  name: string;
  description: string;
  tags: string[];
  created_at: string;
  sources: BaselineSource[];
  app_version: string;
  plugin_versions: Record<string, string>;
  namespaces: Record<string, NamespaceAvailability>;
  entities: BaselineEntity[];
}

export interface BaselineSummaryRow {
  id: string;
  device_id: string;
  name: string;
  description: string;
  created_at: string;
  source_count: number;
  entity_count: number;
}

/** Difference, never health: no DEGRADED/CRITICAL/FAILED here. */
export type EntityDiffState =
  | "UNCHANGED"
  | "ADDED"
  | "REMOVED"
  | "CHANGED"
  | "UNAVAILABLE"
  | "UNKNOWN";

export interface BaselineAttributeDiff {
  attribute: string;
  baseline_value?: AttributeValue | null;
  current_value?: AttributeValue | null;
  absolute_delta?: number | null;
  relative_delta?: number | null;
}

export interface BaselineDiffEntity {
  key: EntityKey;
  display_name: string;
  state: EntityDiffState;
  attribute_diffs: BaselineAttributeDiff[];
  baseline_presence_ratio?: number | null;
  reason?: string | null;
}

export type BaselineCompatibility = "COMPATIBLE" | "PARTIALLY_COMPATIBLE" | "INCOMPATIBLE";

export interface BaselineDiff {
  baseline_id: string;
  device_id: string;
  diagnostic_run_id: string;
  compared_at: string;
  compatibility: BaselineCompatibility;
  compatibility_notes: { code: string; message: string }[];
  entities: BaselineDiffEntity[];
}

export interface CaptureCandidate {
  run_id: string;
  device_id: string;
  started_at: string;
  mode: string;
  overall_health?: string | null;
  entity_count: number;
  unobserved_namespaces: string[];
  warnings: { code: string; message: string }[];
}

export type Requirement = "required" | "optional";

export interface Selector {
  key?: string | null;
  key_prefix?: string | null;
}

/** The closed operator set — profiles are data, never code. */
export type Constraint =
  | { operator: "exists" }
  | { operator: "not_exists" }
  | { operator: "equals"; field: string; value: string | number | boolean }
  | { operator: "not_equals"; field: string; value: string | number | boolean }
  | { operator: "min"; field: string; value: number }
  | { operator: "max"; field: string; value: number }
  | { operator: "between"; field: string; min: number; max: number }
  | { operator: "count_min"; value: number }
  | { operator: "count_max"; value: number }
  | { operator: "contains"; field: string; value: string }
  | { operator: "set_contains"; field: string; value: string }
  | {
      operator: "relationship_exists";
      from: string;
      to: string;
      mode?: "direct_edge" | "path";
    };

export interface Expectation {
  id: string;
  description: string;
  namespace: string;
  kind: string;
  selector: Selector;
  requirement: Requirement;
  constraint: Constraint;
  from_baseline: boolean;
}

export type ProfileStatus = "DRAFT" | "ACTIVE" | "ARCHIVED";

export interface Profile {
  schema_version: number;
  id: string;
  name: string;
  description: string;
  revision: number;
  status: ProfileStatus;
  created_at: string;
  updated_at: string;
  tags: string[];
  expectations: Expectation[];
}

export interface ProfileRow {
  id: string;
  name: string;
  description: string;
  created_at: string;
  latest_revision: number;
}

export interface ProfileRevisionRow {
  revision: number;
  status: string;
  created_at: string;
  updated_at: string;
  evaluation_count: number;
}

export interface DeviceProfileAssignment {
  device_id: string;
  profile_id: string;
  profile_revision: number;
  activated_at: string;
  runtime_id?: string | null;
}

export interface DraftSuggestion {
  expectation: Expectation;
  selected_by_default: boolean;
  rationale: string;
  suggested_threshold: boolean;
}

export interface DraftReport {
  suggestions: DraftSuggestion[];
  skipped: { key: string; reason: string }[];
}

export type ExpectationStatus = "SATISFIED" | "UNSATISFIED" | "UNKNOWN" | "NOT_APPLICABLE";

export interface ExpectationResult {
  expectation_id: string;
  description: string;
  namespace: string;
  kind: string;
  status: ExpectationStatus;
  evaluated_entities: EntityKey[];
  actual?: AttributeValue | null;
  expected: string;
  reason: string;
  evidence_ids: string[];
  evaluated_at: string;
}

export interface EvaluationRun {
  id: string;
  device_id: string;
  diagnostic_run_id: string;
  profile_id: string;
  profile_revision: number;
  baseline_id?: string | null;
  started_at: string;
  finished_at: string;
  app_version: string;
  plugin_versions: Record<string, string>;
  results: ExpectationResult[];
}

export interface ComparisonView {
  run_id: string;
  baseline: BaselineDiff | null;
  evaluation: EvaluationRun | null;
}

/** Structured backend error so the UI can point at the offending field. */
export interface C2ErrorPayload {
  message: string;
  validation_errors: { code: string; path: string; message: string }[];
}
