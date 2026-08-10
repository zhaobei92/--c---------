import { describe, expect, it } from "vitest";
import {
  captureNeedsConfirmation,
  diffCounts,
  evaluationSummary,
  groupByNamespace,
  notableEntities,
  orderedResults,
  renderAttribute,
  renderConstraint,
  renderSubject,
  statusCounts,
} from "../c2Data";
import type {
  BaselineDiff,
  BaselineDiffEntity,
  EvaluationRun,
  Expectation,
  ExpectationResult,
  ExpectationStatus,
} from "../types";

function diffEntity(
  namespace: string,
  kind: string,
  key: string,
  state: BaselineDiffEntity["state"],
): BaselineDiffEntity {
  return {
    key: { namespace, kind, key },
    display_name: key,
    state,
    attribute_diffs: [],
  };
}

function diff(entities: BaselineDiffEntity[]): BaselineDiff {
  return {
    baseline_id: "b1",
    device_id: "local",
    diagnostic_run_id: "r1",
    compared_at: "2026-01-01T00:00:00Z",
    compatibility: "COMPATIBLE",
    compatibility_notes: [],
    entities,
  };
}

function result(id: string, status: ExpectationStatus): ExpectationResult {
  return {
    expectation_id: id,
    description: id,
    namespace: "ros",
    kind: "topic",
    status,
    evaluated_entities: [],
    expected: "exists",
    reason: "",
    evidence_ids: [],
    evaluated_at: "2026-01-01T00:00:00Z",
  };
}

function evaluation(results: ExpectationResult[]): EvaluationRun {
  return {
    id: "e1",
    device_id: "local",
    diagnostic_run_id: "r1",
    profile_id: "p1",
    profile_revision: 2,
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    app_version: "0.1.0",
    plugin_versions: {},
    results,
  };
}

describe("renderAttribute", () => {
  it("renders each typed value without inventing precision", () => {
    expect(renderAttribute({ type: "NUMBER", value: 8 })).toBe("8");
    expect(renderAttribute({ type: "NUMBER", value: 9.75 })).toBe("9.7500");
    expect(renderAttribute({ type: "TEXT", value: "jazzy" })).toBe("jazzy");
    expect(renderAttribute({ type: "BOOL", value: true })).toBe("true");
    expect(renderAttribute(null)).toBe("—");
  });

  it("renders sets in a stable order regardless of arrival order", () => {
    const a = renderAttribute({ type: "TEXT_SET", value: ["b", "a", "c"] });
    const b = renderAttribute({ type: "TEXT_SET", value: ["c", "a", "b"] });
    expect(a).toBe(b);
    expect(a).toBe("a, b, c");
  });
});

describe("diff rendering", () => {
  it("counts every state and separates notable from unchanged", () => {
    const d = diff([
      diffEntity("ros", "node", "/a", "ADDED"),
      diffEntity("ros", "node", "/b", "REMOVED"),
      diffEntity("ros", "topic", "/c", "UNCHANGED"),
      diffEntity("nvidia", "gpu", "gpu-0", "UNAVAILABLE"),
    ]);
    const counts = diffCounts(d);
    expect(counts.ADDED).toBe(1);
    expect(counts.REMOVED).toBe(1);
    expect(counts.UNCHANGED).toBe(1);
    expect(counts.UNAVAILABLE).toBe(1);
    expect(counts.CHANGED).toBe(0);

    expect(notableEntities(d)).toHaveLength(3);
    expect(notableEntities(null)).toEqual([]);
  });

  it("groups by namespace in a deterministic order", () => {
    const grouped = groupByNamespace([
      diffEntity("ros", "node", "/a", "ADDED"),
      diffEntity("nvidia", "gpu", "gpu-0", "ADDED"),
      diffEntity("ros", "node", "/b", "ADDED"),
    ]);
    expect(grouped.map(([ns]) => ns)).toEqual(["nvidia", "ros"]);
    expect(grouped[1][1]).toHaveLength(2);
  });
});

describe("evaluation rendering", () => {
  it("counts statuses without folding UNKNOWN into failures", () => {
    const counts = statusCounts(
      evaluation([
        result("a", "SATISFIED"),
        result("b", "UNSATISFIED"),
        result("c", "UNKNOWN"),
        result("d", "UNKNOWN"),
        result("e", "NOT_APPLICABLE"),
      ]),
    );
    expect(counts).toEqual({
      SATISFIED: 1,
      UNSATISFIED: 1,
      UNKNOWN: 2,
      NOT_APPLICABLE: 1,
    });
  });

  it("puts unsatisfied first, then unknown, then the rest", () => {
    const ordered = orderedResults(
      evaluation([
        result("satisfied", "SATISFIED"),
        result("na", "NOT_APPLICABLE"),
        result("unknown", "UNKNOWN"),
        result("unsatisfied", "UNSATISFIED"),
      ]),
    );
    expect(ordered.map((r) => r.expectation_id)).toEqual([
      "unsatisfied",
      "unknown",
      "na",
      "satisfied",
    ]);
  });

  it("summarises with counts and never a score", () => {
    const summary = evaluationSummary(
      evaluation([result("a", "SATISFIED"), result("b", "UNKNOWN")]),
    );
    expect(summary).toContain("1 satisfied");
    expect(summary).toContain("1 not observed");
    expect(summary).not.toMatch(/%|score/i);
    expect(evaluationSummary(null)).toBe("No profile evaluated for this run");
  });
});

describe("expectation rendering", () => {
  const expectation = (
    constraint: Expectation["constraint"],
    selector: Expectation["selector"] = {},
  ): Expectation => ({
    id: "e",
    description: "",
    namespace: "ros",
    kind: "topic",
    selector,
    requirement: "required",
    constraint,
    from_baseline: false,
  });

  it("renders every operator", () => {
    expect(renderConstraint({ operator: "exists" })).toBe("exists");
    expect(renderConstraint({ operator: "not_exists" })).toBe("does not exist");
    expect(renderConstraint({ operator: "min", field: "hz", value: 8 })).toBe("hz >= 8");
    expect(renderConstraint({ operator: "max", field: "hz", value: 30 })).toBe("hz <= 30");
    expect(renderConstraint({ operator: "between", field: "hz", min: 8, max: 30 })).toBe(
      "8 <= hz <= 30",
    );
    expect(renderConstraint({ operator: "equals", field: "type", value: "a" })).toBe("type == a");
    expect(renderConstraint({ operator: "not_equals", field: "type", value: "a" })).toBe(
      "type != a",
    );
    expect(renderConstraint({ operator: "count_min", value: 2 })).toBe("count >= 2");
    expect(renderConstraint({ operator: "count_max", value: 4 })).toBe("count <= 4");
    expect(renderConstraint({ operator: "contains", field: "name", value: "cam" })).toBe(
      "name contains 'cam'",
    );
    expect(renderConstraint({ operator: "set_contains", field: "ipv4", value: "10.0.0.1" })).toBe(
      "ipv4 includes '10.0.0.1'",
    );
    expect(
      renderConstraint({ operator: "relationship_exists", from: "map", to: "base", mode: "path" }),
    ).toBe("path map -> base");
    expect(
      renderConstraint({
        operator: "relationship_exists",
        from: "map",
        to: "base",
        mode: "direct_edge",
      }),
    ).toBe("direct edge map -> base");
  });

  it("describes what an expectation applies to", () => {
    expect(renderSubject(expectation({ operator: "exists" }, { key: "/scan" }))).toBe(
      "ros.topic /scan",
    );
    expect(renderSubject(expectation({ operator: "exists" }, { key_prefix: "/camera/" }))).toBe(
      "ros.topic /camera/*",
    );
    expect(renderSubject(expectation({ operator: "count_min", value: 1 }))).toBe("ros.topic any");
  });
});

describe("capture confirmation", () => {
  it("asks before capturing anything less than a clean, fully observed run", () => {
    const clean = {
      overall_health: "HEALTHY",
      warnings: [],
      unobserved_namespaces: [],
    };
    expect(captureNeedsConfirmation(clean)).toBe(false);
    expect(captureNeedsConfirmation({ ...clean, overall_health: "DEGRADED" })).toBe(true);
    expect(captureNeedsConfirmation({ ...clean, warnings: [{ code: "RUN_UNFINISHED" }] })).toBe(
      true,
    );
    expect(captureNeedsConfirmation({ ...clean, unobserved_namespaces: ["ros"] })).toBe(true);
  });
});

describe("large payloads (§60)", () => {
  const bigEvaluation = (): EvaluationRun => {
    const statuses: ExpectationStatus[] = [
      "SATISFIED",
      "UNSATISFIED",
      "UNKNOWN",
      "NOT_APPLICABLE",
    ];
    return evaluation(
      Array.from({ length: 1000 }, (_, i) =>
        result(`expectation-${String(i).padStart(4, "0")}`, statuses[i % 4]),
      ),
    );
  };

  const bigDiff = (): BaselineDiff => {
    const states: BaselineDiffEntity["state"][] = ["UNCHANGED", "ADDED", "CHANGED", "REMOVED"];
    return diff(
      Array.from({ length: 1000 }, (_, i) =>
        diffEntity(
          i % 3 === 0 ? "ros" : i % 3 === 1 ? "system" : "network",
          "node",
          `/entity_${i}`,
          states[i % 4],
        ),
      ),
    );
  };

  // Measurement, not a benchmark: these bounds are loose enough to be
  // stable on slow CI and tight enough to catch an accidental O(n²).
  it("prepares 1000 expectations for display quickly", () => {
    const run = bigEvaluation();
    const started = performance.now();
    const ordered = orderedResults(run);
    const counts = statusCounts(run);
    const elapsed = performance.now() - started;

    expect(ordered).toHaveLength(1000);
    expect(counts.SATISFIED + counts.UNSATISFIED + counts.UNKNOWN + counts.NOT_APPLICABLE).toBe(
      1000,
    );
    // Attention-needing outcomes still sort to the front at this size.
    expect(ordered[0].status).toBe("UNSATISFIED");
    expect(elapsed).toBeLessThan(200);
  });

  it("prepares 1000 comparison entities for display quickly", () => {
    const d = bigDiff();
    const started = performance.now();
    const counts = diffCounts(d);
    const grouped = groupByNamespace(notableEntities(d));
    const elapsed = performance.now() - started;

    expect(counts.UNCHANGED).toBe(250);
    expect(grouped.map(([ns]) => ns)).toEqual(["network", "ros", "system"]);
    expect(grouped.reduce((n, [, items]) => n + items.length, 0)).toBe(750);
    expect(elapsed).toBeLessThan(200);
  });
});
