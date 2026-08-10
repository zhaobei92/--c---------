import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { formatDuration } from "../format";
import type {
  DiagnosticMode,
  HealthState,
  RunFilter,
  RunSummaryRow,
  StoredRun,
} from "../types";
import { HealthBadge, SeverityChip, StatusChip } from "../components/badges";
import { EvidenceView } from "../components/EvidenceView";

const PAGE_SIZE = 20;

interface Props {
  /** Bumped by the app whenever a run completes, to refresh the list. */
  refreshToken: number;
  storageOk: boolean;
}

export function HistoryPage({ refreshToken, storageOk }: Props) {
  const [rows, setRows] = useState<RunSummaryRow[]>([]);
  const [mode, setMode] = useState<DiagnosticMode | "">("");
  const [health, setHealth] = useState<HealthState | "">("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<StoredRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const filter: RunFilter = {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    };
    if (mode) filter.mode = mode;
    if (health) filter.health = health;
    api
      .listHistory(filter)
      .then(setRows)
      .catch((e) => setError(String(e)));
  }, [mode, health, page]);

  useEffect(load, [load, refreshToken]);

  const openRun = (runId: string) => {
    api
      .getHistoryRun(runId)
      .then(setSelected)
      .catch((e) => setError(String(e)));
  };

  const deleteRun = (runId: string) => {
    api
      .deleteHistoryRun(runId)
      .then(() => {
        setSelected(null);
        load();
      })
      .catch((e) => setError(String(e)));
  };

  if (!storageOk) {
    return (
      <div className="page">
        <header className="page-header">
          <h1>History</h1>
        </header>
        <p className="muted">
          The history database could not be opened — diagnostics still run,
          but results are not persisted. Check the application logs.
        </p>
      </div>
    );
  }

  if (selected) {
    const run = selected.run;
    return (
      <div className="page">
        <header className="page-header">
          <h1>Run {run.id.slice(0, 8)}</h1>
          <div className="actions">
            <button className="btn btn-small" onClick={() => setSelected(null)}>
              ← Back to history
            </button>
            <button className="btn btn-small btn-danger" onClick={() => deleteRun(run.id)}>
              Delete run
            </button>
          </div>
        </header>

        <div className="card run-summary">
          <span>
            {run.mode} · started {new Date(run.started_at).toLocaleString()} · app v
            {selected.app_version}
          </span>
          {selected.overall_health && <HealthBadge health={selected.overall_health} />}
        </div>

        {selected.plugin_snapshots.length > 0 && (
          <div className="card">
            <h3>Plugins used in this run</h3>
            <table className="table table-plain">
              <tbody>
                {selected.plugin_snapshots.map((snap) => (
                  <tr key={snap.plugin_id}>
                    <td className="obs-key">{snap.plugin_id}</td>
                    <td>
                      v{snap.version} · API {snap.api_version}
                      {snap.capabilities.length > 0 && (
                        <span className="muted"> · {snap.capabilities.join(", ")}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="check-list">
          {run.results.map((result) => (
            <HistoricalResult key={`${result.check_id}-${result.started_at}`} result={result} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>History</h1>
        <div className="actions">
          <select
            className="btn"
            value={mode}
            onChange={(e) => {
              setPage(0);
              setMode(e.target.value as DiagnosticMode | "");
            }}
          >
            <option value="">All modes</option>
            <option value="QUICK">QUICK</option>
            <option value="FULL">FULL</option>
          </select>
          <select
            className="btn"
            value={health}
            onChange={(e) => {
              setPage(0);
              setHealth(e.target.value as HealthState | "");
            }}
          >
            <option value="">Any health</option>
            <option value="HEALTHY">HEALTHY</option>
            <option value="DEGRADED">DEGRADED</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="UNKNOWN">UNKNOWN</option>
          </select>
        </div>
      </header>

      {error && <p className="check-error">{error}</p>}

      <table className="table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Device</th>
            <th>Mode</th>
            <th>Health</th>
            <th>Duration</th>
            <th>Checks</th>
            <th>Findings</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="row-link" onClick={() => openRun(row.id)}>
              <td>{new Date(row.started_at).toLocaleString()}</td>
              <td>{row.device_name}</td>
              <td>{row.mode}</td>
              <td>
                {row.overall_health ? (
                  <HealthBadge health={row.overall_health as HealthState} />
                ) : (
                  <span className="badge badge-muted">{row.status}</span>
                )}
              </td>
              <td>{row.duration_ms != null ? formatDuration(row.duration_ms) : "–"}</td>
              <td>{row.check_count}</td>
              <td>{row.finding_count > 0 ? row.finding_count : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p className="muted">No diagnostic runs match this filter yet.</p>
      )}

      <div className="actions">
        <button
          className="btn btn-small"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          ← Newer
        </button>
        <span className="muted">page {page + 1}</span>
        <button
          className="btn btn-small"
          disabled={rows.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          Older →
        </button>
      </div>
    </div>
  );
}

/** Stored result: same layout as live results, purely from the database. */
function HistoricalResult({ result }: { result: StoredRun["run"]["results"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`check-card ${result.findings.length ? "check-card-flagged" : ""}`}>
      <button className="check-header" onClick={() => setOpen(!open)}>
        <span className="check-name">
          {result.check_id}
          <span className="check-id">{result.plugin_id}</span>
        </span>
        <span className="check-meta">
          <span className="check-duration">{formatDuration(result.duration_ms)}</span>
          <StatusChip status={result.status} />
        </span>
      </button>
      {result.error && <div className="check-error">{result.error.message}</div>}
      {open && (
        <div className="check-body">
          {result.findings.length > 0 && (
            <section>
              <h4>Findings</h4>
              {result.findings.map((f) => (
                <div key={f.id} className="finding">
                  <div className="finding-head">
                    <SeverityChip severity={f.severity} />
                    <span className="finding-code">{f.code}</span>
                    <strong>{f.title}</strong>
                  </div>
                  <p>{f.detail}</p>
                </div>
              ))}
            </section>
          )}
          {result.observations.length > 0 && (
            <section>
              <h4>Observations</h4>
              <table className="obs-table">
                <tbody>
                  {result.observations.map((o) => (
                    <tr key={o.id}>
                      <td className="obs-key">{o.key}</td>
                      <td>
                        {o.value.type === "JSON"
                          ? JSON.stringify(o.value.value)
                          : String(o.value.value)}
                        {o.unit ? ` ${o.unit}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {result.evidence.length > 0 && (
            <section>
              <h4>Evidence</h4>
              {result.evidence.map((e) => (
                <EvidenceView key={e.id} evidence={e} />
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
