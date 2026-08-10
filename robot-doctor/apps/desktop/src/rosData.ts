// Pure helpers to extract ROS observation payloads from CheckResults.
// Kept free of React/Tauri imports so they are unit-testable.

import type {
  CheckResult,
  RosDiagnosticStatus,
  RosGraphSnapshot,
  RosRuntimeConfig,
  RosTfSnapshot,
} from "./types";

export function observationJson(result: CheckResult | null, key: string): unknown {
  if (!result) return undefined;
  const obs = result.observations.find((o) => o.key === key);
  return obs && obs.value.type === "JSON" ? obs.value.value : undefined;
}

export function observationNumber(result: CheckResult | null, key: string): number | undefined {
  const obs = result?.observations.find((o) => o.key === key);
  return obs && obs.value.type === "NUMBER" ? obs.value.value : undefined;
}

export function graphSnapshot(result: CheckResult | null): RosGraphSnapshot | null {
  return (observationJson(result, "ros.graph.snapshot") as RosGraphSnapshot) ?? null;
}

export function tfSnapshot(result: CheckResult | null): RosTfSnapshot | null {
  return (observationJson(result, "ros.tf.snapshot") as RosTfSnapshot) ?? null;
}

export function discoveredRuntimes(result: CheckResult | null): RosRuntimeConfig[] {
  return (observationJson(result, "ros.runtimes.discovered") as RosRuntimeConfig[]) ?? [];
}

export function environmentInfo(result: CheckResult | null): Record<string, unknown> | null {
  return (observationJson(result, "ros.env.info") as Record<string, unknown>) ?? null;
}

export function diagnosticStatuses(result: CheckResult | null): RosDiagnosticStatus[] {
  return (observationJson(result, "ros.diagnostics.statuses") as RosDiagnosticStatus[]) ?? [];
}

export function lifecycleStates(result: CheckResult | null): Record<string, unknown>[] {
  return (observationJson(result, "ros.lifecycle.states") as Record<string, unknown>[]) ?? [];
}

export const DIAG_LEVEL_LABELS: Record<number, string> = {
  0: "OK",
  1: "WARN",
  2: "ERROR",
  3: "STALE",
};

/** Case-insensitive substring filter over a list by a projection. */
export function filterBy<T>(items: T[], query: string, project: (item: T) => string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => project(item).toLowerCase().includes(needle));
}
