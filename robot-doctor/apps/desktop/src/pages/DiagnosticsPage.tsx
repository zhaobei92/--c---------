import type { RunView } from "../diagnosisStore";
import { runProgress } from "../diagnosisStore";
import type { DiagnosticMode, EvaluationRun } from "../types";
import { HealthBadge } from "../components/badges";
import { CheckResultCard } from "../components/CheckResultCard";
import { EvaluationPanel } from "../components/EvaluationPanel";

interface Props {
  run: RunView | null;
  /** Expectation outcomes for this run, once the engine has evaluated. */
  evaluation: EvaluationRun | null;
  onRunDiagnosis: (mode: DiagnosticMode) => void;
  onCancel: () => void;
}

export function DiagnosticsPage({ run, evaluation, onRunDiagnosis, onCancel }: Props) {
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
          {run?.running && (
            <button className="btn btn-danger" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </header>
      {run && !run.running && run.persisted === false && (
        <div className="error-banner">
          This run could not be saved to the history database — results are
          only available until the app closes.
        </div>
      )}

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
          {!run.running && (
            <EvaluationPanel
              evaluation={evaluation}
              emptyHint="No profile is active. Activate one on the Profiles page to have every run checked against explicit expectations."
            />
          )}
        </>
      )}
    </div>
  );
}
