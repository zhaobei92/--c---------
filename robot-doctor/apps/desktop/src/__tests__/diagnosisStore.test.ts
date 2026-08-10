import { describe, expect, it } from "vitest";
import { applyEvent, collectFindings, runProgress, type RunView } from "../diagnosisStore";
import type { CheckDefinition, CheckResult, DiagnosisEvent, Finding } from "../types";

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

function result(checkId: string, findings: Finding[] = []): CheckResult {
  return {
    check_id: checkId,
    plugin_id: "system",
    device_id: "local",
    status: findings.length ? "FAILED" : "PASSED",
    started_at: "2026-01-01T00:00:00Z",
    duration_ms: 12,
    observations: [],
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

const started: DiagnosisEvent = {
  type: "RUN_STARTED",
  run_id: "r1",
  device_id: "local",
  mode: "QUICK",
  planned: [def("system.cpu"), def("system.memory")],
};

describe("diagnosisStore", () => {
  it("builds pending rows from the plan", () => {
    const view = applyEvent(null, started)!;
    expect(view.checks).toHaveLength(2);
    expect(view.checks.every((c) => c.state === "PENDING")).toBe(true);
    expect(runProgress(view)).toEqual({ done: 0, total: 2 });
  });

  it("tracks progressive completion", () => {
    let view: RunView | null = applyEvent(null, started);
    view = applyEvent(view, { type: "CHECK_STARTED", run_id: "r1", check_id: "system.cpu" });
    expect(view!.checks[0].state).toBe("RUNNING");

    view = applyEvent(view, {
      type: "CHECK_COMPLETED",
      run_id: "r1",
      result: result("system.cpu"),
    });
    expect(view!.checks[0].state).toBe("DONE");
    expect(view!.checks[0].result?.status).toBe("PASSED");
    expect(runProgress(view)).toEqual({ done: 1, total: 2 });

    view = applyEvent(view, {
      type: "CHECK_COMPLETED",
      run_id: "r1",
      result: result("system.memory"),
    });
    view = applyEvent(view, {
      type: "RUN_COMPLETED",
      run_id: "r1",
      health: "HEALTHY",
      finished_at: "2026-01-01T00:01:00Z",
    });
    expect(view!.running).toBe(false);
    expect(view!.health).toBe("HEALTHY");
  });

  it("ignores events from other runs", () => {
    let view: RunView | null = applyEvent(null, started);
    const before = view;
    view = applyEvent(view, { type: "CHECK_STARTED", run_id: "other", check_id: "system.cpu" });
    expect(view).toBe(before);
  });

  it("accepts results for checks that were not in the plan", () => {
    let view: RunView | null = applyEvent(null, started);
    view = applyEvent(view, {
      type: "CHECK_COMPLETED",
      run_id: "r1",
      result: result("system.surprise"),
    });
    expect(view!.checks).toHaveLength(3);
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
});
