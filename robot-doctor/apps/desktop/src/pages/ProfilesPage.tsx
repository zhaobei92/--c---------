import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { renderConstraint, renderSubject } from "../c2Data";
import type {
  BaselineSummaryRow,
  DeviceProfileAssignment,
  DraftReport,
  Profile,
  ProfileRevisionRow,
  ProfileRow,
} from "../types";

type ValidationError = { code: string; path: string; message: string };

export function ProfilesPage({ storageOk }: { storageOk: boolean }) {
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  const [baselines, setBaselines] = useState<BaselineSummaryRow[]>([]);
  const [assignment, setAssignment] = useState<DeviceProfileAssignment | null>(null);
  const [selected, setSelected] = useState<ProfileRow | null>(null);
  const [revisions, setRevisions] = useState<ProfileRevisionRow[]>([]);
  const [openProfile, setOpenProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<DraftReport | null>(null);
  const [draftBaseline, setDraftBaseline] = useState("");
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [draftName, setDraftName] = useState("");
  const [yaml, setYaml] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationError[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = (e: unknown) => {
    const payload = api.parseC2Error(e);
    setError(payload.message);
    setValidation(payload.validation_errors);
  };

  const refresh = useCallback(() => {
    if (!storageOk) return;
    api.listProfiles().then(setProfiles).catch(fail);
    api.listBaselines().then(setBaselines).catch(fail);
    api.getActiveProfile().then(setAssignment).catch(fail);
  }, [storageOk]);

  useEffect(refresh, [refresh]);

  const openRevisions = async (row: ProfileRow) => {
    setSelected(row);
    setOpenProfile(null);
    setError(null);
    try {
      setRevisions(await api.listProfileRevisions(row.id));
    } catch (e) {
      fail(e);
    }
  };

  const openRevision = async (revision: number) => {
    if (!selected) return;
    try {
      const profile = await api.getProfile(selected.id, revision);
      setOpenProfile(profile);
      setYaml(await api.exportProfile(selected.id, revision));
    } catch (e) {
      fail(e);
    }
  };

  const generateDraft = async () => {
    if (!draftBaseline) return;
    setError(null);
    setValidation([]);
    setBusy(true);
    try {
      const report = await api.draftProfile(draftBaseline);
      setDraft(report);
      setAccepted(
        new Set(
          report.suggestions.filter((s) => s.selected_by_default).map((s) => s.expectation.id),
        ),
      );
      const source = baselines.find((b) => b.id === draftBaseline);
      setDraftName(source ? `${source.name} profile` : "Drafted profile");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const toggleAccepted = (id: string) => {
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const saveDraft = async () => {
    if (!draft) return;
    setError(null);
    setValidation([]);
    setBusy(true);
    try {
      const now = new Date().toISOString();
      const profile: Profile = {
        schema_version: 1,
        id: draftName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-") || "drafted-profile",
        name: draftName || "Drafted profile",
        description: "Drafted from a known-good baseline and reviewed before saving.",
        // The backend allocates the real revision; a draft always starts
        // as DRAFT and only becomes ACTIVE when explicitly activated.
        revision: 0,
        status: "DRAFT",
        created_at: now,
        updated_at: now,
        tags: ["drafted"],
        expectations: draft.suggestions
          .filter((s) => accepted.has(s.expectation.id))
          .map((s) => s.expectation),
      };
      const saved = await api.saveProfile(profile);
      setNotice(`Saved "${saved.name}" revision ${saved.revision} as DRAFT.`);
      setDraft(null);
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const importYaml = async () => {
    setError(null);
    setValidation([]);
    setBusy(true);
    try {
      const imported = await api.importProfile(yaml);
      setNotice(
        `Imported "${imported.name}" as revision ${imported.revision} (DRAFT). ` +
          "Activate it when you are ready for it to be evaluated.",
      );
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const activate = async (profileId: string, revision: number) => {
    setError(null);
    try {
      setAssignment(await api.activateProfile(profileId, revision));
      setNotice(`Revision ${revision} is now evaluated after every diagnostic run.`);
      if (selected) setRevisions(await api.listProfileRevisions(selected.id));
    } catch (e) {
      fail(e);
    }
  };

  const archive = async (profileId: string, revision: number) => {
    try {
      await api.setProfileStatus(profileId, revision, "ARCHIVED");
      if (selected) setRevisions(await api.listProfileRevisions(selected.id));
    } catch (e) {
      fail(e);
    }
  };

  const removeRevision = async (profileId: string, revision: number) => {
    try {
      await api.deleteProfileRevision(profileId, revision);
      if (selected) setRevisions(await api.listProfileRevisions(selected.id));
    } catch (e) {
      // A revision with stored evaluations is history, not garbage.
      fail(e);
    }
  };

  if (!storageOk) {
    return (
      <section className="page">
        <h1>Profiles</h1>
        <p className="muted">History storage is unavailable, so profiles cannot be stored.</p>
      </section>
    );
  }

  return (
    <section className="page">
      <h1>Profiles</h1>
      <p className="muted">
        A profile states what <em>should</em> be true about this machine. Expectations are data,
        not code: they select entities and apply one of a fixed set of operators.
      </p>
      {error && (
        <div className="error-banner">
          {error}
          {validation.length > 0 && (
            <ul className="plain-list">
              {validation.map((v, i) => (
                <li key={`${v.path}-${i}`}>
                  <code>{v.path}</code> — {v.message} <span className="muted">({v.code})</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="card">
        <h2>Active profile</h2>
        {assignment ? (
          <p>
            <strong>{assignment.profile_id}</strong> revision {assignment.profile_revision},
            activated {new Date(assignment.activated_at).toLocaleString()}
            {assignment.runtime_id ? ` · runtime ${assignment.runtime_id}` : ""}
          </p>
        ) : (
          <p className="muted">
            None. Diagnostic runs still work; nothing is evaluated against expectations.
          </p>
        )}
      </div>

      <div className="card">
        <h2>Draft from a baseline</h2>
        {baselines.length === 0 ? (
          <p className="muted">Capture a baseline first.</p>
        ) : (
          <div className="form-row">
            <select value={draftBaseline} onChange={(e) => setDraftBaseline(e.target.value)}>
              <option value="">Choose a baseline…</option>
              {baselines.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name} ({b.source_count} run{b.source_count === 1 ? "" : "s"})
                </option>
              ))}
            </select>
            <button className="btn btn-primary" disabled={busy || !draftBaseline} onClick={generateDraft}>
              Generate suggestions
            </button>
          </div>
        )}

        {draft && (
          <>
            <p className="muted">
              Suggestions only. Entities present in every known-good run are pre-selected;
              anything variable or seen once is offered but left unticked, and thresholds are
              never derived from a single sample.
            </p>
            <div className="form-row">
              <input
                placeholder="Profile name"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
              />
              <button className="btn btn-primary" disabled={busy || accepted.size === 0} onClick={saveDraft}>
                Save {accepted.size} expectation{accepted.size === 1 ? "" : "s"} as draft
              </button>
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th>Subject</th>
                  <th>Expectation</th>
                  <th>Requirement</th>
                  <th>Why suggested</th>
                </tr>
              </thead>
              <tbody>
                {draft.suggestions.map((suggestion) => (
                  <tr key={suggestion.expectation.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={accepted.has(suggestion.expectation.id)}
                        onChange={() => toggleAccepted(suggestion.expectation.id)}
                      />
                    </td>
                    <td>{renderSubject(suggestion.expectation)}</td>
                    <td>
                      {renderConstraint(suggestion.expectation.constraint)}
                      {suggestion.suggested_threshold && (
                        <span className="chip chip-suggested">suggested</span>
                      )}
                    </td>
                    <td>{suggestion.expectation.requirement}</td>
                    <td className="muted">{suggestion.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {draft.skipped.length > 0 && (
              <details>
                <summary>{draft.skipped.length} deliberately not suggested</summary>
                <ul className="plain-list">
                  {draft.skipped.map((s) => (
                    <li key={s.key}>
                      <code>{s.key}</code> — {s.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h2>Saved profiles</h2>
        {profiles.length === 0 ? (
          <p className="muted">None saved yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Id</th>
                <th>Latest revision</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.map((row) => (
                <tr key={row.id}>
                  <td>{row.name}</td>
                  <td>
                    <code>{row.id}</code>
                  </td>
                  <td>{row.latest_revision}</td>
                  <td>
                    <button className="btn btn-small" onClick={() => openRevisions(row)}>Revisions</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {selected && (
          <div className="subsection">
            <h3>{selected.name} — revisions</h3>
            <table className="table">
              <thead>
                <tr>
                  <th>Revision</th>
                  <th>Status</th>
                  <th>Updated</th>
                  <th>Evaluations</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {revisions.map((rev) => {
                  const active =
                    assignment?.profile_id === selected.id &&
                    assignment.profile_revision === rev.revision;
                  return (
                    <tr key={rev.revision}>
                      <td>{rev.revision}</td>
                      <td>
                        <span className={`chip status-${rev.status.toLowerCase()}`}>
                          {rev.status}
                        </span>
                        {active && <span className="chip chip-active">in use</span>}
                      </td>
                      <td>{new Date(rev.updated_at).toLocaleString()}</td>
                      <td>{rev.evaluation_count}</td>
                      <td>
                        <button className="btn btn-small" onClick={() => openRevision(rev.revision)}>View</button>{" "}
                        <button className="btn btn-small" onClick={() => activate(selected.id, rev.revision)}>
                          Activate
                        </button>{" "}
                        <button className="btn btn-small" onClick={() => archive(selected.id, rev.revision)}>Archive</button>{" "}
                        <button
                          className="btn btn-small" disabled={rev.evaluation_count > 0}
                          title={
                            rev.evaluation_count > 0
                              ? "Referenced by stored evaluations — archive it instead"
                              : undefined
                          }
                          onClick={() => removeRevision(selected.id, rev.revision)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {openProfile && (
          <div className="subsection">
            <h3>
              {openProfile.name} revision {openProfile.revision}
            </h3>
            <table className="table">
              <thead>
                <tr>
                  <th>Id</th>
                  <th>Subject</th>
                  <th>Expectation</th>
                  <th>Requirement</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {openProfile.expectations.map((expectation) => (
                  <tr key={expectation.id}>
                    <td>
                      <code>{expectation.id}</code>
                    </td>
                    <td>{renderSubject(expectation)}</td>
                    <td>{renderConstraint(expectation.constraint)}</td>
                    <td>{expectation.requirement}</td>
                    <td className="muted">
                      {expectation.from_baseline ? "from baseline" : "hand-written"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2>YAML</h2>
        <p className="muted">
          Export produces a deterministic document, so profiles diff cleanly in version control.
          Imported profiles always land as drafts.
        </p>
        <textarea
          className="yaml-editor"
          rows={16}
          value={yaml}
          spellCheck={false}
          onChange={(e) => setYaml(e.target.value)}
          placeholder="Paste a profile document here to import it…"
        />
        <div className="form-row">
          <button className="btn btn-primary" disabled={busy || !yaml.trim()} onClick={importYaml}>
            Import as draft
          </button>
          <button
            className="btn btn-small" disabled={!yaml}
            onClick={() => navigator.clipboard?.writeText(yaml).catch(() => undefined)}
          >
            Copy
          </button>
        </div>
      </div>
    </section>
  );
}
