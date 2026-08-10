import { useState } from "react";
import type { CheckRow } from "../diagnosisStore";
import { formatDuration } from "../format";
import { SeverityChip, StatusChip } from "./badges";
import { EvidenceView } from "./EvidenceView";

/** One check in the diagnostics list: status, duration, expandable
 *  findings / observations / evidence. */
export function CheckResultCard({ row }: { row: CheckRow }) {
  const [open, setOpen] = useState(false);
  const result = row.result;
  const name = row.definition?.name ?? row.checkId;

  return (
    <div className={`check-card ${result?.findings.length ? "check-card-flagged" : ""}`}>
      <button className="check-header" onClick={() => setOpen(!open)} disabled={!result}>
        <span className="check-name">
          {name}
          <span className="check-id">{row.checkId}</span>
        </span>
        <span className="check-meta">
          {result ? (
            <>
              <span className="check-duration">{formatDuration(result.duration_ms)}</span>
              <StatusChip status={result.status} />
            </>
          ) : row.state === "RUNNING" ? (
            <span className="badge badge-running">RUNNING…</span>
          ) : (
            <span className="badge badge-muted">PENDING</span>
          )}
        </span>
      </button>

      {result?.error && (
        <div className="check-error">{result.error.message}</div>
      )}

      {open && result && (
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
                  <div className="finding-subject">subject: {f.subject}</div>
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
