import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import {
  diffCounts,
  groupByNamespace,
  notableEntities,
  renderAttribute,
  captureNeedsConfirmation,
} from "../c2Data";
import type {
  Baseline,
  BaselineDiff,
  BaselineSummaryRow,
  CaptureCandidate,
  EntityDiffState,
} from "../types";

/** A baseline diff states differences; it never grades them. */
const DIFF_CLASS: Record<EntityDiffState, string> = {
  ADDED: "diff-added",
  REMOVED: "diff-removed",
  CHANGED: "diff-changed",
  UNAVAILABLE: "diff-unavailable",
  UNKNOWN: "diff-unknown",
  UNCHANGED: "diff-unchanged",
};

export function BaselinesPage({ storageOk }: { storageOk: boolean }) {
  const [baselines, setBaselines] = useState<BaselineSummaryRow[]>([]);
  const [candidates, setCandidates] = useState<CaptureCandidate[]>([]);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [detail, setDetail] = useState<Baseline | null>(null);
  const [diff, setDiff] = useState<BaselineDiff | null>(null);
  const [diffRunId, setDiffRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showUnchanged, setShowUnchanged] = useState(false);

  const refresh = useCallback(() => {
    if (!storageOk) return;
    api.listBaselines().then(setBaselines).catch((e) => setError(api.parseC2Error(e).message));
    api
      .listBaselineCandidates(20)
      .then(setCandidates)
      .catch((e) => setError(api.parseC2Error(e).message));
  }, [storageOk]);

  useEffect(refresh, [refresh]);

  const toggleRun = (runId: string) => {
    setSelectedRuns((current) =>
      current.includes(runId) ? current.filter((id) => id !== runId) : [...current, runId],
    );
  };

  const capture = async () => {
    setError(null);
    setNotice(null);
    const chosen = candidates.filter((c) => selectedRuns.includes(c.run_id));
    // Capture is never blocked — the user is the authority on what
    // "known good" means — but a run with warnings must be acknowledged.
    const risky = chosen.filter(captureNeedsConfirmation);
    if (risky.length > 0) {
      const detail = risky
        .map((c) => {
          const reasons = [
            ...c.warnings.map((w) => w.message),
            ...c.unobserved_namespaces.map((ns) => `${ns} could not be observed`),
            ...(c.overall_health && c.overall_health !== "HEALTHY"
              ? [`run health was ${c.overall_health}`]
              : []),
          ];
          return `• ${c.started_at}: ${reasons.join("; ")}`;
        })
        .join("\n");
      if (!window.confirm(`Capture anyway?\n\n${detail}`)) return;
    }
    setBusy(true);
    try {
      const baseline = await api.captureBaseline(
        name || "Known good",
        description,
        selectedRuns,
        risky.length > 0,
      );
      setNotice(
        `Captured "${baseline.name}" from ${baseline.sources.length} run(s), ` +
          `${baseline.entities.length} entities.`,
      );
      setSelectedRuns([]);
      setName("");
      setDescription("");
      refresh();
    } catch (e) {
      setError(api.parseC2Error(e).message);
    } finally {
      setBusy(false);
    }
  };

  const open = async (id: string) => {
    setError(null);
    setDiff(null);
    try {
      setDetail(await api.getBaseline(id));
    } catch (e) {
      setError(api.parseC2Error(e).message);
    }
  };

  const compare = async () => {
    if (!detail || !diffRunId) return;
    setError(null);
    setBusy(true);
    try {
      setDiff(await api.diffRunAgainstBaseline(detail.id, diffRunId));
    } catch (e) {
      setError(api.parseC2Error(e).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Delete this baseline? Runs it was captured from are not affected.")) {
      return;
    }
    try {
      await api.deleteBaseline(id);
      if (detail?.id === id) {
        setDetail(null);
        setDiff(null);
      }
      refresh();
    } catch (e) {
      setError(api.parseC2Error(e).message);
    }
  };

  if (!storageOk) {
    return (
      <section className="page">
        <h1>Baselines</h1>
        <p className="muted">
          History storage is unavailable, so baselines cannot be captured or compared.
        </p>
      </section>
    );
  }

  const counts = diffCounts(diff);
  const visible = showUnchanged ? (diff?.entities ?? []) : notableEntities(diff);

  return (
    <section className="page">
      <h1>Baselines</h1>
      <p className="muted">
        A baseline records what this machine looked like when it was known good. Comparing a
        later run against it states what changed — it does not decide whether a change is a
        problem.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="card">
        <h2>Capture from known-good runs</h2>
        {candidates.length === 0 ? (
          <p className="muted">No diagnostic runs recorded yet.</p>
        ) : (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th>Started</th>
                  <th>Mode</th>
                  <th>Health</th>
                  <th>Entities</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((candidate) => (
                  <tr key={candidate.run_id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedRuns.includes(candidate.run_id)}
                        onChange={() => toggleRun(candidate.run_id)}
                      />
                    </td>
                    <td>{new Date(candidate.started_at).toLocaleString()}</td>
                    <td>{candidate.mode}</td>
                    <td>{candidate.overall_health ?? "—"}</td>
                    <td>{candidate.entity_count}</td>
                    <td className="muted">
                      {[
                        ...candidate.warnings.map((w) => w.message),
                        ...candidate.unobserved_namespaces.map((ns) => `${ns} not observed`),
                      ].join("; ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="form-row">
              <input
                placeholder="Baseline name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <input
                placeholder="Description (optional)"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
              <button
               className="btn btn-primary"
                disabled={busy || selectedRuns.length === 0}
                onClick={capture}
              >
                Capture baseline ({selectedRuns.length} run
                {selectedRuns.length === 1 ? "" : "s"})
              </button>
            </div>
            <p className="muted">
              Selecting several runs captures a baseline set: entities present in every run are
              marked stable, and ones that come and go are recorded as variable.
            </p>
          </>
        )}
      </div>

      <div className="card">
        <h2>Saved baselines</h2>
        {baselines.length === 0 ? (
          <p className="muted">None captured yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Captured</th>
                <th>Source runs</th>
                <th>Entities</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {baselines.map((row) => (
                <tr key={row.id}>
                  <td>{row.name}</td>
                  <td>{new Date(row.created_at).toLocaleString()}</td>
                  <td>{row.source_count}</td>
                  <td>{row.entity_count}</td>
                  <td>
                    <button className="btn btn-small" onClick={() => open(row.id)}>Open</button>{" "}
                    <button className="btn btn-small" onClick={() => remove(row.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detail && (
        <div className="card">
          <h2>{detail.name}</h2>
          <p className="muted">
            Captured {new Date(detail.created_at).toLocaleString()} on app v
            {detail.app_version} from{" "}
            {detail.sources.map((s) => new Date(s.run_started_at).toLocaleString()).join(", ")}
          </p>

          <div className="form-row">
            <select value={diffRunId} onChange={(e) => setDiffRunId(e.target.value)}>
              <option value="">Compare against a run…</option>
              {candidates.map((c) => (
                <option key={c.run_id} value={c.run_id}>
                  {new Date(c.started_at).toLocaleString()} ({c.mode})
                </option>
              ))}
            </select>
            <button className="btn btn-primary" disabled={busy || !diffRunId} onClick={compare}>
              Compare
            </button>
          </div>

          {diff && (
            <>
              <div className="chip-row">
                <span className={`chip compat-${diff.compatibility.toLowerCase()}`}>
                  {diff.compatibility.replace(/_/g, " ")}
                </span>
                <span className="chip">{counts.ADDED} added</span>
                <span className="chip">{counts.REMOVED} removed</span>
                <span className="chip">{counts.CHANGED} changed</span>
                <span className="chip">{counts.UNAVAILABLE} unavailable</span>
                <span className="chip">{counts.UNKNOWN} unknown</span>
                <span className="chip">{counts.UNCHANGED} unchanged</span>
              </div>
              {diff.compatibility_notes.length > 0 && (
                <ul className="note-list">
                  {diff.compatibility_notes.map((note) => (
                    <li key={note.code}>
                      <code>{note.code}</code> {note.message}
                    </li>
                  ))}
                </ul>
              )}
              <label className="inline-label">
                <input
                  type="checkbox"
                  checked={showUnchanged}
                  onChange={(e) => setShowUnchanged(e.target.checked)}
                />
                Show unchanged entities
              </label>
              {groupByNamespace(visible).map(([namespace, entities]) => (
                <div key={namespace} className="subsection">
                  <h3>{namespace}</h3>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>State</th>
                        <th>Entity</th>
                        <th>Attribute changes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entities.map((entity) => (
                        <tr key={`${entity.key.kind}:${entity.key.key}`}>
                          <td>
                            <span className={`chip ${DIFF_CLASS[entity.state]}`}>
                              {entity.state}
                            </span>
                          </td>
                          <td>
                            <div>{entity.display_name}</div>
                            <div className="muted small">
                              {entity.key.kind}
                              {entity.baseline_presence_ratio != null &&
                              entity.baseline_presence_ratio < 1
                                ? ` · present in ${Math.round(
                                    entity.baseline_presence_ratio * 100,
                                  )}% of known-good runs`
                                : ""}
                            </div>
                            {entity.reason && <div className="muted small">{entity.reason}</div>}
                          </td>
                          <td>
                            {entity.attribute_diffs.length === 0 ? (
                              <span className="muted">—</span>
                            ) : (
                              <ul className="plain-list">
                                {entity.attribute_diffs.map((change) => (
                                  <li key={change.attribute}>
                                    <code>{change.attribute}</code>{" "}
                                    {renderAttribute(change.baseline_value)} →{" "}
                                    {renderAttribute(change.current_value)}
                                    {change.relative_delta != null && (
                                      <span className="muted">
                                        {" "}
                                        ({(change.relative_delta * 100).toFixed(1)}%)
                                      </span>
                                    )}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
              {visible.length === 0 && (
                <p className="muted">Nothing differs from the baseline.</p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
