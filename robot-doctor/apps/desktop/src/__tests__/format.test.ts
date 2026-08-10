import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, formatPercent, formatUptime } from "../format";

describe("format helpers", () => {
  it("formats bytes with binary units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KiB");
    expect(formatBytes(16 * 1024 ** 3)).toBe("16.0 GiB");
    expect(formatBytes(-1)).toBe("–");
  });

  it("formats durations", () => {
    expect(formatDuration(250)).toBe("250 ms");
    expect(formatDuration(2500)).toBe("2.5 s");
    expect(formatDuration(95_000)).toBe("1 min 35 s");
  });

  it("formats uptime", () => {
    expect(formatUptime(59)).toBe("0m");
    expect(formatUptime(3 * 3600 + 5 * 60)).toBe("3h 5m");
    expect(formatUptime(2 * 86400 + 3600)).toBe("2d 1h 0m");
  });

  it("formats percentages", () => {
    expect(formatPercent(42.123)).toBe("42.1%");
    expect(formatPercent(Number.NaN)).toBe("–");
  });
});
