import { describe, expect, it } from "vitest";
import {
  diagnosticStatuses,
  discoveredRuntimes,
  filterBy,
  graphSnapshot,
} from "../rosData";
import type { CheckResult } from "../types";

function resultWith(observations: { key: string; value: unknown }[]): CheckResult {
  return {
    check_id: "ros.graph",
    plugin_id: "ros2",
    device_id: "local",
    status: "PASSED",
    started_at: "2026-01-01T00:00:00Z",
    duration_ms: 10,
    observations: observations.map((o, i) => ({
      id: `o${i}`,
      check_id: "ros.graph",
      key: o.key,
      value: { type: "JSON", value: o.value },
      evidence_ids: [],
      observed_at: "2026-01-01T00:00:00Z",
    })),
    evidence: [],
    findings: [],
  };
}

describe("rosData", () => {
  it("extracts the graph snapshot observation", () => {
    const result = resultWith([
      {
        key: "ros.graph.snapshot",
        value: { runtime_id: "r", provider: "fixture", nodes: [], topics: [] },
      },
    ]);
    expect(graphSnapshot(result)?.provider).toBe("fixture");
    expect(graphSnapshot(null)).toBeNull();
  });

  it("extracts discovered runtimes and diagnostics", () => {
    const result = resultWith([
      { key: "ros.runtimes.discovered", value: [{ id: "auto:jazzy", name: "Jazzy" }] },
      {
        key: "ros.diagnostics.statuses",
        value: [{ name: "lidar", level: 2, hardware_id: "h", message: "", values: {}, source_topic: "/diagnostics" }],
      },
    ]);
    expect(discoveredRuntimes(result)[0].id).toBe("auto:jazzy");
    expect(diagnosticStatuses(result)[0].level).toBe(2);
  });

  it("filters case-insensitively", () => {
    const items = [{ n: "/scan" }, { n: "/odom" }];
    expect(filterBy(items, "SCAN", (i) => i.n)).toHaveLength(1);
    expect(filterBy(items, "", (i) => i.n)).toHaveLength(2);
  });
});
