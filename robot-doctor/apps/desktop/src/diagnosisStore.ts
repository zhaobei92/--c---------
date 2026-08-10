// Pure reducer that folds progressive DiagnosisEvents into a renderable
// run view. Kept free of React/Tauri imports so it is unit-testable.

import type {
  CheckDefinition,
  CheckResult,
  DiagnosisEvent,
  DiagnosticMode,
  HealthState,
} from "./types";

export type CheckRowState = "QUEUED" | "RUNNING" | "DONE" | "SKIPPED";

export interface CheckRow {
  checkId: string;
  definition?: CheckDefinition;
  state: CheckRowState;
  result?: CheckResult;
  /** Present for SKIPPED rows: why the check did not execute. */
  skipReason?: string;
  skipPrerequisite?: string;
}

export interface RunView {
  runId: string;
  deviceId: string;
  mode: DiagnosticMode;
  running: boolean;
  health?: HealthState;
  finishedAt?: string;
  /** False when the backend reported a persistence failure for this run. */
  persisted?: boolean;
  /** Ordered check rows: planned order, results attached as they arrive. */
  checks: CheckRow[];
}

/** Fold one event into the current view. Events for other runs are ignored. */
export function applyEvent(view: RunView | null, event: DiagnosisEvent): RunView | null {
  switch (event.type) {
    case "RUN_STARTED":
      return {
        runId: event.run_id,
        deviceId: event.device_id,
        mode: event.mode,
        running: true,
        checks: event.planned.map((definition) => ({
          checkId: definition.id,
          definition,
          state: "QUEUED",
        })),
      };
    case "CHECK_QUEUED":
    case "PLUGIN_STARTED":
    case "PLUGIN_COMPLETED":
      return view;
    case "CHECK_STARTED": {
      if (!view || view.runId !== event.run_id) return view;
      return {
        ...view,
        checks: view.checks.map((row) =>
          row.checkId === event.check_id && row.state === "QUEUED"
            ? { ...row, state: "RUNNING" }
            : row,
        ),
      };
    }
    case "CHECK_COMPLETED": {
      if (!view || view.runId !== event.run_id) return view;
      const known = view.checks.some((row) => row.checkId === event.result.check_id);
      const checks = known
        ? view.checks.map((row) =>
            row.checkId === event.result.check_id
              ? { ...row, state: "DONE" as const, result: event.result }
              : row,
          )
        : [
            ...view.checks,
            { checkId: event.result.check_id, state: "DONE" as const, result: event.result },
          ];
      return { ...view, checks };
    }
    case "CHECK_SKIPPED": {
      if (!view || view.runId !== event.run_id) return view;
      const known = view.checks.some((row) => row.checkId === event.check_id);
      const skipped = {
        state: "SKIPPED" as const,
        result: event.result,
        skipReason: event.reason,
        skipPrerequisite: event.prerequisite ?? undefined,
      };
      const checks = known
        ? view.checks.map((row) =>
            row.checkId === event.check_id ? { ...row, ...skipped } : row,
          )
        : [...view.checks, { checkId: event.check_id, ...skipped }];
      return { ...view, checks };
    }
    case "RUN_COMPLETED": {
      if (!view || view.runId !== event.run_id) return view;
      return {
        ...view,
        running: false,
        health: event.health,
        finishedAt: event.finished_at,
        persisted: event.persisted,
        // Anything not finished when the run ends did not produce a result.
        checks: view.checks.map((row) =>
          row.state === "DONE" || row.state === "SKIPPED"
            ? row
            : { ...row, state: "DONE" as const },
        ),
      };
    }
  }
}

export interface RunProgress {
  done: number;
  total: number;
}

export function runProgress(view: RunView | null): RunProgress {
  if (!view) return { done: 0, total: 0 };
  return {
    done: view.checks.filter((c) => c.state === "DONE" || c.state === "SKIPPED").length,
    total: view.checks.length,
  };
}

/** Findings across all completed checks, most severe first. */
export function collectFindings(view: RunView | null) {
  if (!view) return [];
  const rank = { CRITICAL: 0, ERROR: 1, WARNING: 2, INFO: 3 } as const;
  return view.checks
    .flatMap((c) => c.result?.findings ?? [])
    .sort((a, b) => rank[a.severity] - rank[b.severity]);
}

/** Extract a numeric observation by key from any completed check. */
export function numberObservation(view: RunView | null, key: string): number | undefined {
  if (!view) return undefined;
  for (const row of view.checks) {
    const hit = row.result?.observations.find((o) => o.key === key);
    if (hit && hit.value.type === "NUMBER") return hit.value.value;
  }
  return undefined;
}

/** Extract a text observation by key from any completed check. */
export function textObservation(view: RunView | null, key: string): string | undefined {
  if (!view) return undefined;
  for (const row of view.checks) {
    const hit = row.result?.observations.find((o) => o.key === key);
    if (hit && hit.value.type === "TEXT") return hit.value.value;
  }
  return undefined;
}

export interface GpuInfo {
  index: number;
  model?: string;
  uuid?: string;
  memoryTotal?: number;
  memoryUsed?: number;
  utilization?: number;
  temperature?: number;
  power?: number;
}

/** Collect per-GPU stats from nvidia.* observations (multi-GPU aware). */
export function collectGpus(view: RunView | null): GpuInfo[] {
  if (!view) return [];
  const gpus = new Map<number, GpuInfo>();
  const get = (index: number): GpuInfo => {
    let gpu = gpus.get(index);
    if (!gpu) {
      gpu = { index };
      gpus.set(index, gpu);
    }
    return gpu;
  };
  for (const row of view.checks) {
    for (const obs of row.result?.observations ?? []) {
      const match = obs.key.match(/^nvidia\.gpu(\d+)\.(.+)$/);
      if (!match) continue;
      const gpu = get(Number(match[1]));
      const field = match[2];
      if (obs.value.type === "NUMBER") {
        if (field === "memory_total_bytes") gpu.memoryTotal = obs.value.value;
        else if (field === "memory_used_bytes") gpu.memoryUsed = obs.value.value;
        else if (field === "utilization_percent") gpu.utilization = obs.value.value;
        else if (field === "temperature_celsius") gpu.temperature = obs.value.value;
        else if (field === "power_watts") gpu.power = obs.value.value;
      } else if (obs.value.type === "TEXT") {
        if (field === "model") gpu.model = obs.value.value;
        else if (field === "uuid") gpu.uuid = obs.value.value;
      }
    }
  }
  return [...gpus.values()].sort((a, b) => a.index - b.index);
}
