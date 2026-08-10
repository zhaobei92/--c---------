import { describe, expect, it } from "vitest";
import { formatTargets, parseTargets } from "../pages/SettingsPage";

describe("network target parsing", () => {
  it("parses hosts, host:port and timeouts", () => {
    const targets = parseTargets(
      "192.168.1.1\nrobot.local:22 2000\n# comment\n\n10.0.0.5:8765",
    );
    expect(targets).toEqual([
      { host: "192.168.1.1" },
      { host: "robot.local", port: 22, timeout_ms: 2000 },
      { host: "10.0.0.5", port: 8765 },
    ]);
  });

  it("keeps invalid port text as part of the host", () => {
    const targets = parseTargets("host:notaport");
    expect(targets).toEqual([{ host: "host:notaport" }]);
  });

  it("round-trips through formatTargets", () => {
    const text = "192.168.1.1\nrobot.local:22 2000";
    expect(formatTargets(parseTargets(text))).toBe(text);
  });
});
