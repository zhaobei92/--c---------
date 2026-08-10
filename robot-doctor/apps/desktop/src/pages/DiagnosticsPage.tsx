import type { RunView } from "../diagnosisStore";
import { runProgress } from "../diagnosisStore";
import type { DiagnosticMode } from "../types";
import { HealthBadge } from "../components/badges";
import { CheckResultCard } from "../components/CheckResultCard";

interface Props {
  run: RunView | null;
  onRunDiagnosis: (mode: DiagnosticMode) => void;
}

export function DiagnosticsPage({ run, onRunDiagnosis }: Props) {
  const progress = runProgress(run);
  return (
    <div className="page">
      <header className="page-header">
        <h1>Diagnostics</h1>
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

      {!run ? (
        <p className="muted">
          No diagnosis yet. Run a quick diagnosis for a fast inventory, or a full
          diagnosis for deeper checks.
        </p>
      ) : (
        <>
          <div className="card run-summary">
            <span>
              {run.mode} run · {progress.done}/{progress.total} checks
              {run.running ? " · running…" : ""}
            </span>
            {run.health && <HealthBadge health={run.health} />}
          </div>
          {run.running && (
            <div className="progress">
              <div
                className="progress-bar"
                style={{ width: progress.total ? `${(progress.done / progress.total) * 100}%` : "0%" }}
              />
            </div>
          )}
          <div className="check-list">
            {run.checks.map((row) => (
              <CheckResultCard key={row.checkId} row={row} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
