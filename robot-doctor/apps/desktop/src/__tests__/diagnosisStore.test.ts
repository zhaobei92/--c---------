import { describe, expect, it } from "vitest";
import {
  applyEvent,
  collectFindings,
  collectGpus,
  runProgress,
  type RunView,
} from "../diagnosisStore";
import type {
  CheckDefinition,
  CheckResult,
  DiagnosisEvent,
  Finding,
  Observation,
} from "../types";

function def(id: string): CheckDefinition {
  return {
    id,
    plugin_id: "system",
    name: id,
    description: "",
    cost: "FAST",
    timeout_ms: 5000,
    platforms: [],
    depends_on: [],
    modes: ["QUICK", "FULL"],
  };
}

function result(
  checkId: string,
  findings: Finding[] = [],
  observations: Observation[] = [],
): CheckResult {
  return {
    check_id: checkId,
    plugin_id: "system",
    device_id: "local",
    status: findings.length ? "FAILED" : "PASSED",
    started_at: "2026-01-01T00:00:00Z",
    duration_ms: 12,
    observations,
    evidence: [],
    findings,
  };
}

function finding(severity: Finding["severity"], code: string): Finding {
  return {
    id: `f-${code}-${severity}`,
    device_id: "local",
    check_id: "system.disk",
    severity,
    code,
    title: code,
    detail: "",
    subject: "disk:/",
    evidence_ids: ["e1"],
    detected_at: "2026-01-01T00:00:00Z",
  };
}

function numberObs(key: string, value: number): Observation {
  return {
    id: `o-${key}`,
    check_id: "nvidia.devices",
    key,
    value: { type: "NUMBER", value },
    evidence_ids: ["e1"],
    observed_at: "2026-01-01T00:00:00Z",
  };
}

function textObs(key: string, value: string): Observation {
  return {
    id: `o-${key}`,
    check_id: "nvidia.devices",
    key,
    value: { type: "TEXT", value },
    evidence_ids: ["e1"],
    observed_at: "2026-01-01T00:00:00Z",
  };
}

const started: DiagnosisEvent = {
  type: "RUN_STARTED",
  run_id: "r1",
  device_id: "local",
  mode: "QUICK",
  planned: [def("system.cpu"), def("system.memory")],
};

describe("diagnosisStore", () => {
  it("builds queued rows from the plan", () => {
    const view = applyEvent(null, started)!;
    expect(view.checks).toHaveLength(2);
    expect(view.checks.every((c) => c.state === "QUEUED")).toBe(true);
    expect(runProgress(view)).toEqual({ done: 0, total: 2 });
  });

  it("tracks progressive completion including skips", () => {
    let view: RunView | null = applyEvent(null, started);
    view = applyEvent(view, { type: "CHECK_STARTED", run_id: "r1", check_id: "system.cpu" });
    expect(view!.checks[0].state).toBe("RUNNING");

    view = applyEvent(view, {
      type: "CHECK_COMPLETED",
      run_id: "r1",
      result: result("system.cpu"),
    });
    expect(view!.checks[0].state).toBe("DONE");
    expect(runProgress(view)).toEqual({ done: 1, total: 2 });

    view = applyEvent(view, {
      type: "CHECK_SKIPPED",
      run_id: "r1",
      check_id: "system.memory",
      reason: "prerequisite 'system.cpu' did not pass",
      prerequisite: "system.cpu",
      result: { ...result("system.memory"), status: "DEPENDENCY_MISSING" },
    });
    expect(view!.checks[1].state).toBe("SKIPPED");
    expect(view!.checks[1].skipPrerequisite).toBe("system.cpu");
    expect(runProgress(view)).toEqual({ done: 2, total: 2 });

    view = applyEvent(view, {
      type: "RUN_COMPLETED",
      run_id: "r1",
      health: "HEALTHY",
      finished_at: "2026-01-01T00:01:00Z",
      persisted: true,
    });
    expect(view!.running).toBe(false);
    expect(view!.persisted).toBe(true);
    // A skip stays a skip after run completion.
    expect(view!.checks[1].state).toBe("SKIPPED");
  });

  it("ignores events from other runs", () => {
    let view: RunView | null = applyEvent(null, started);
    const before = view;
    view = applyEvent(view, { type: "CHECK_STARTED", run_id: "other", check_id: "system.cpu" });
    expect(view).toBe(before);
  });

  it("surfaces persistence failure", () => {
    let view: RunView | null = applyEvent(null, started);
    view = applyEvent(view, {
      type: "RUN_COMPLETED",
      run_id: "r1",
      health: "HEALTHY",
      finished_at: "2026-01-01T00:01:00Z",
      persisted: false,
    });
    expect(view!.persisted).toBe(false);
  });

  it("sorts findings most severe first", () => {
    let view: RunView | null = applyEvent(null, started);
    view = applyEvent(view, {
      type: "CHECK_COMPLETED",
      run_id: "r1",
      result: result("system.cpu", [
        finding("WARNING", "MEMORY_PRESSURE"),
        finding("CRITICAL", "DISK_LOW"),
      ]),
    });
    const findings = collectFindings(view);
    expect(findings.map((f) => f.severity)).toEqual(["CRITICAL", "WARNING"]);
  });

  it("collects multi-GPU stats from nvidia observations", () => {
    let view: RunView | null = applyEvent(null, started);
    view = applyEvent(view, {
      type: "CHECK_COMPLETED",
      run_id: "r1",
      result: result(
        "nvidia.devices",
        [],
        [
          textObs("nvidia.gpu0.model", "RTX 5090"),
          numberObs("nvidia.gpu0.memory_total_bytes", 32 * 1024 ** 3),
          numberObs("nvidia.gpu0.temperature_celsius", 47),
          textObs("nvidia.gpu1.model", "RTX 4000 Ada"),
          numberObs("nvidia.gpu1.utilization_percent", 23),
        ],
      ),
    });
    const gpus = collectGpus(view);
    expect(gpus).toHaveLength(2);
    expect(gpus[0].model).toBe("RTX 5090");
    expect(gpus[0].temperature).toBe(47);
    expect(gpus[1].index).toBe(1);
    expect(gpus[1].utilization).toBe(23);
  });
});
