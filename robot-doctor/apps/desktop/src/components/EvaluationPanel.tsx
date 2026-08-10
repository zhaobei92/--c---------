import { evaluationSummary, orderedResults, renderAttribute, statusCounts } from "../c2Data";
import type { EvaluationRun, ExpectationStatus } from "../types";

const STATUS_CLASS: Record<ExpectationStatus, string> = {
  SATISFIED: "expect-satisfied",
  UNSATISFIED: "expect-unsatisfied",
  UNKNOWN: "expect-unknown",
  NOT_APPLICABLE: "expect-na",
};

/**
 * Expectation outcomes for one run.
 *
 * UNKNOWN is rendered as its own state, never merged into failures: it
 * means Robot Doctor could not observe the subject, which is a statement
 * about the diagnosis, not about the machine.
 */
export function EvaluationPanel({
  evaluation,
  emptyHint,
}: {
  evaluation: EvaluationRun | null;
  emptyHint?: string;
}) {
  if (!evaluation) {
    return (
      <div className="card">
        <h2>Expectations</h2>
        <p className="muted">
          {emptyHint ?? "No profile is active, so nothing was evaluated for this run."}
        </p>
      </div>
    );
  }

  const counts = statusCounts(evaluation);
  const results = orderedResults(evaluation);

  return (
    <div className="card">
      <h2>Expectations</h2>
      <p className="muted">
        Profile <code>{evaluation.profile_id}</code> revision {evaluation.profile_revision} ·{" "}
        {evaluationSummary(evaluation)}
      </p>
      <div className="chip-row">
        <span className="chip expect-satisfied">{counts.SATISFIED} satisfied</span>
        <span className="chip expect-unsatisfied">{counts.UNSATISFIED} unsatisfied</span>
        <span className="chip expect-unknown">{counts.UNKNOWN} not observed</span>
        <span className="chip expect-na">{counts.NOT_APPLICABLE} not applicable</span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Expectation</th>
            <th>Expected</th>
            <th>Actual</th>
            <th>Why</th>
          </tr>
        </thead>
        <tbody>
          {results.map((result) => (
            <tr key={result.expectation_id}>
              <td>
                <span className={`chip ${STATUS_CLASS[result.status]}`}>
                  {result.status.replace(/_/g, " ")}
                </span>
              </td>
              <td>
                <div>{result.description || result.expectation_id}</div>
                <div className="muted small">
                  {result.namespace}.{result.kind}
                </div>
              </td>
              <td>{result.expected}</td>
              <td>{result.actual ? renderAttribute(result.actual) : "—"}</td>
              <td className="muted">
                {result.reason}
                {result.evaluated_entities.length > 0 && (
                  <details>
                    <summary>{result.evaluated_entities.length} entities checked</summary>
                    <ul className="plain-list">
                      {result.evaluated_entities.map((key) => (
                        <li key={`${key.kind}:${key.key}`}>
                          <code>
                            {key.namespace}.{key.kind}:{key.key}
                          </code>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {results.length === 0 && <p className="muted">The profile contains no expectations.</p>}
    </div>
  );
}
