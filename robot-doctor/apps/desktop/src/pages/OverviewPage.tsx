import type { RunView } from "../diagnosisStore";
import { collectFindings, runProgress } from "../diagnosisStore";
import { formatBytes, formatPercent, formatUptime } from "../format";
import type { Device, DiagnosticMode, Observation } from "../types";
import { HealthBadge, SeverityChip } from "../components/badges";

interface Props {
  devices: Device[];
  run: RunView | null;
  onRunDiagnosis: (mode: DiagnosticMode) => void;
  onOpenDiagnostics: () => void;
}

/** Pull a named observation out of the current/last run. */
function findObservation(run: RunView | null, key: string): Observation | undefined {
  if (!run) return undefined;
  for (const row of run.checks) {
    const hit = row.result?.observations.find((o) => o.key === key);
    if (hit) return hit;
  }
  return undefined;
}

function numberOf(run: RunView | null, key: string): number | undefined {
  const obs = findObservation(run, key);
  return obs && obs.value.type === "NUMBER" ? obs.value.value : undefined;
}

function textOf(run: RunView | null, key: string): string | undefined {
  const obs = findObservation(run, key);
  return obs && obs.value.type === "TEXT" ? obs.value.value : undefined;
}

export function OverviewPage({ devices, run, onRunDiagnosis, onOpenDiagnostics }: Props) {
  const local = devices.find((d) => d.endpoint.type === "LOCAL");
  const progress = runProgress(run);
  const findings = collectFindings(run);

  const cpu = numberOf(run, "system.cpu.usage_percent");
  const memPercent = numberOf(run, "system.memory.used_percent");
  const memTotal = numberOf(run, "system.memory.total_bytes");
  const uptime = numberOf(run, "system.uptime.seconds");
  const hostname = textOf(run, "system.identity.hostname");
  const os = textOf(run, "system.os.long_version");

  return (
    <div className="page">
      <header className="page-header">
        <h1>Overview</h1>
        <div className="actions">
          <button
            className="btn btn-primary"
            onClick={() => onRunDiagnosis("QUICK")}
            disabled={run?.running ?? false}
          >
            Run quick diagnosis
          </button>
          <button
            className="btn"
            onClick={() => onRunDiagnosis("FULL")}
            disabled={run?.running ?? false}
          >
            Run full diagnosis
          </button>
        </div>
      </header>

      {local && (
        <div className="card device-card">
          <div className="device-title">
            <h2>{hostname ?? local.name}</h2>
            <HealthBadge health={run?.health ?? local.health} />
          </div>
          <div className="device-sub">{os ?? "Run a diagnosis to populate system information."}</div>
          {run?.running && (
            <div className="progress">
              <div
                className="progress-bar"
                style={{ width: progress.total ? `${(progress.done / progress.total) * 100}%` : "0%" }}
              />
              <span className="progress-text">
                {progress.done}/{progress.total} checks
              </span>
            </div>
          )}
        </div>
      )}

      <div className="stat-grid">
        <div className="card stat">
          <span className="stat-label">CPU</span>
          <span className="stat-value">{cpu !== undefined ? formatPercent(cpu) : "–"}</span>
        </div>
        <div className="card stat">
          <span className="stat-label">Memory</span>
          <span className="stat-value">
            {memPercent !== undefined ? formatPercent(memPercent) : "–"}
          </span>
          <span className="stat-sub">{memTotal !== undefined ? `of ${formatBytes(memTotal)}` : ""}</span>
        </div>
        <div className="card stat">
          <span className="stat-label">Uptime</span>
          <span className="stat-value">{uptime !== undefined ? formatUptime(uptime) : "–"}</span>
        </div>
        <div className="card stat">
          <span className="stat-label">Capabilities</span>
          <span className="stat-value">{local?.capabilities.length ?? 0}</span>
          <span className="stat-sub">{local?.capabilities.map((c) => c.name).join(", ")}</span>
        </div>
      </div>

      <div className="card">
        <div className="card-title-row">
          <h3>Findings</h3>
          {run && (
            <button className="btn btn-small" onClick={onOpenDiagnostics}>
              View diagnostics
            </button>
          )}
        </div>
        {findings.length === 0 ? (
          <p className="muted">
            {run
              ? run.running
                ? "Diagnosis in progress…"
                : "No findings. All evaluated checks are within expectations."
              : "No diagnosis has been run yet."}
          </p>
        ) : (
          findings.map((f) => (
            <div key={f.id} className="finding">
              <div className="finding-head">
                <SeverityChip severity={f.severity} />
                <span className="finding-code">{f.code}</span>
                <strong>{f.title}</strong>
              </div>
              <p>{f.detail}</p>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
