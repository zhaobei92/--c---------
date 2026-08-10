// Pure reducer that folds progressive DiagnosisEvents into a renderable
// run view. Kept free of React/Tauri imports so it is unit-testable.

import type {
  CheckDefinition,
  CheckResult,
  DiagnosisEvent,
  DiagnosticMode,
  HealthState,
} from "./types";

export type CheckRowState = "PENDING" | "RUNNING" | "DONE";

export interface CheckRow {
  checkId: string;
  definition?: CheckDefinition;
  state: CheckRowState;
  result?: CheckResult;
}

export interface RunView {
  runId: string;
  deviceId: string;
  mode: DiagnosticMode;
  running: boolean;
  health?: HealthState;
  finishedAt?: string;
  /** Ordered check rows: planned order, results attached as they arrive. */
  checks: CheckRow[];
}

export function emptyRunView(): RunView | null {
  return null;
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
          state: "PENDING",
        })),
      };
    case "CHECK_STARTED": {
      if (!view || view.runId !== event.run_id) return view;
      return {
        ...view,
        checks: view.checks.map((row) =>
          row.checkId === event.check_id && row.state === "PENDING"
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
    case "RUN_COMPLETED": {
      if (!view || view.runId !== event.run_id) return view;
      return {
        ...view,
        running: false,
        health: event.health,
        finishedAt: event.finished_at,
        // Anything still pending when the run ends did not produce a result.
        checks: view.checks.map((row) =>
          row.state === "DONE" ? row : { ...row, state: "DONE" as const },
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
    done: view.checks.filter((c) => c.state === "DONE").length,
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
