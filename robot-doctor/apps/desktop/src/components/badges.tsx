import type { CheckStatus, HealthState, Severity } from "../types";

const HEALTH_CLASS: Record<HealthState, string> = {
  HEALTHY: "badge badge-ok",
  DEGRADED: "badge badge-warn",
  CRITICAL: "badge badge-crit",
  OFFLINE: "badge badge-off",
  UNKNOWN: "badge badge-muted",
};

export function HealthBadge({ health }: { health: HealthState }) {
  return <span className={HEALTH_CLASS[health]}>{health}</span>;
}

const STATUS_CLASS: Record<CheckStatus, string> = {
  PASSED: "badge badge-ok",
  FAILED: "badge badge-crit",
  UNAVAILABLE: "badge badge-muted",
  UNSUPPORTED: "badge badge-muted",
  TIMEOUT: "badge badge-warn",
  PERMISSION_DENIED: "badge badge-warn",
  DEPENDENCY_MISSING: "badge badge-muted",
  OFFLINE: "badge badge-off",
  ERROR: "badge badge-warn",
};

export function StatusChip({ status }: { status: CheckStatus }) {
  return <span className={STATUS_CLASS[status]}>{status}</span>;
}

const SEVERITY_CLASS: Record<Severity, string> = {
  INFO: "badge badge-muted",
  WARNING: "badge badge-warn",
  ERROR: "badge badge-warn",
  CRITICAL: "badge badge-crit",
};

export function SeverityChip({ severity }: { severity: Severity }) {
  return <span className={SEVERITY_CLASS[severity]}>{severity}</span>;
}
