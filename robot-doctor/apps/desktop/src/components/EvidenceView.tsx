import { useState } from "react";
import type { Evidence } from "../types";

/** Inspectable evidence: summary line expanding to the raw captured data. */
export function EvidenceView({ evidence }: { evidence: Evidence }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="evidence">
      <button className="evidence-toggle" onClick={() => setOpen(!open)}>
        <span className="evidence-kind">{evidence.kind}</span>
        <span className="evidence-source">{evidence.source}</span>
        <span>{evidence.summary}</span>
        <span className="evidence-caret">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <pre className="evidence-data">{JSON.stringify(evidence.data, null, 2)}</pre>
      )}
    </div>
  );
}
