import type { RunView } from "../diagnosisStore";
import {
  collectFindings,
  collectGpus,
  numberObservation,
  runProgress,
  textObservation,
} from "../diagnosisStore";
import { formatBytes, formatPercent, formatUptime } from "../format";
import type { Device, DiagnosticMode } from "../types";
import { HealthBadge, SeverityChip } from "../components/badges";

interface Props {
  devices: Device[];
  run: RunView | null;
  onRunDiagnosis: (mode: DiagnosticMode) => void;
  onOpenDiagnostics: () => void;
}

export function OverviewPage({ devices, run, onRunDiagnosis, onOpenDiagnostics }: Props) {
  const local = devices.find((d) => d.endpoint.type === "LOCAL");
  const progress = runProgress(run);
  const findings = collectFindings(run);
  const gpus = collectGpus(run);

  const cpu = numberObservation(run, "system.cpu.usage_percent");
  const memPercent = numberObservation(run, "system.memory.used_percent");
  const memTotal = numberObservation(run, "system.memory.total_bytes");
  const uptime = numberObservation(run, "system.uptime.seconds");
  const hostname = textObservation(run, "system.identity.hostname");
  const os = textObservation(run, "system.os.long_version");
  const ifaceCount = numberObservation(run, "network.interfaces.up_non_loopback");
  const gateway = textObservation(run, "network.default_route.gateway");
  const routePresent = textObservation(run, "network.default_route.present");

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
          <span className="stat-label">Network</span>
          <span className="stat-value">
            {ifaceCount !== undefined ? `${ifaceCount} up` : "–"}
          </span>
          <span className="stat-sub">
            {routePresent === "false"
              ? "no default route"
              : gateway
                ? `gw ${gateway}`
                : ""}
          </span>
        </div>
      </div>

      {gpus.length > 0 && (
        <div className="card">
          <h3>NVIDIA</h3>
          <div className="stat-grid">
            {gpus.map((gpu) => (
              <div key={gpu.index} className="card stat">
                <span className="stat-label">
                  GPU {gpu.index}
                  {gpus.length > 1 ? ` · ${gpu.uuid?.slice(0, 12) ?? ""}` : ""}
                </span>
                <span className="stat-value">{gpu.model ?? "NVIDIA GPU"}</span>
                <span className="stat-sub">
                  {[
                    gpu.memoryUsed !== undefined && gpu.memoryTotal !== undefined
                      ? `VRAM ${formatBytes(gpu.memoryUsed)} / ${formatBytes(gpu.memoryTotal)}`
                      : gpu.memoryTotal !== undefined
                        ? `VRAM ${formatBytes(gpu.memoryTotal)}`
                        : null,
                    gpu.utilization !== undefined ? `GPU ${formatPercent(gpu.utilization)}` : null,
                    gpu.temperature !== undefined ? `${gpu.temperature.toFixed(0)}°C` : null,
                    gpu.power !== undefined ? `${gpu.power.toFixed(0)} W` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

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
