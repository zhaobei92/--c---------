import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import {
  DIAG_LEVEL_LABELS,
  diagnosticStatuses,
  discoveredRuntimes,
  environmentInfo,
  filterBy,
  graphSnapshot,
  lifecycleStates,
  tfSnapshot,
} from "../rosData";
import type {
  CheckResult,
  RosGraphSnapshot,
  RosRuntimeConfig,
  RosTopicInfo,
} from "../types";
import { StatusChip } from "../components/badges";
import { EvidenceView } from "../components/EvidenceView";

type Tab = "runtime" | "graph" | "topics" | "tf" | "diagnostics" | "lifecycle";

export function RosPage() {
  const [tab, setTab] = useState<Tab>("runtime");
  const [runtime, setRuntime] = useState<RosRuntimeConfig | null>(null);
  const [envResult, setEnvResult] = useState<CheckResult | null>(null);
  const [graphResult, setGraphResult] = useState<CheckResult | null>(null);
  const [tfResult, setTfResult] = useState<CheckResult | null>(null);
  const [diagResult, setDiagResult] = useState<CheckResult | null>(null);
  const [lifecycleResult, setLifecycleResult] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshEnvironment = useCallback(() => {
    setBusy("environment");
    api
      .runSingleCheck("ros.environment", {})
      .then(setEnvResult)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(null));
  }, []);

  useEffect(() => {
    api
      .getRosRuntime()
      .then((r) => setRuntime((r as RosRuntimeConfig) ?? null))
      .catch(() => {});
    refreshEnvironment();
  }, [refreshEnvironment]);

  const selectRuntime = (config: RosRuntimeConfig) => {
    api
      .setRosRuntime(config)
      .then(() => {
        setRuntime(config);
        refreshEnvironment();
      })
      .catch((e) => setError(String(e)));
  };

  const refreshGraph = () => {
    setBusy("graph");
    api
      .runSingleCheck("ros.graph", {})
      .then(setGraphResult)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(null));
  };

  const refreshTf = () => {
    setBusy("tf");
    api
      .runSingleCheck("ros.tf", {})
      .then(setTfResult)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(null));
  };

  const refreshDiagnostics = () => {
    setBusy("diagnostics");
    api
      .runSingleCheck("ros.diagnostics", { window_s: 2.0 })
      .then(setDiagResult)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(null));
  };

  const refreshLifecycle = () => {
    setBusy("lifecycle");
    api
      .runSingleCheck("ros.lifecycle", {})
      .then(setLifecycleResult)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(null));
  };

  const snapshot = graphSnapshot(graphResult);
  const unavailable =
    envResult && envResult.status !== "PASSED" && envResult.status !== "FAILED";

  return (
    <div className="page">
      <header className="page-header">
        <h1>ROS</h1>
        <div className="actions">
          {envResult && <StatusChip status={envResult.status} />}
          {busy && <span className="badge badge-running">{busy}…</span>}
        </div>
      </header>

      <div className="tab-row">
        {(["runtime", "graph", "topics", "tf", "diagnostics", "lifecycle"] as Tab[]).map(
          (t) => (
            <button
              key={t}
              className={`nav-item tab ${tab === t ? "nav-item-active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "tf" ? "TF" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ),
        )}
      </div>

      {error && <p className="check-error">{error}</p>}
      {unavailable && (
        <div className="card">
          <span className="badge badge-muted">UNAVAILABLE</span>
          <p className="muted" style={{ marginTop: 8 }}>
            {envResult?.error?.message ??
              "ROS runtime not found. Configure one in the Runtime tab."}
          </p>
        </div>
      )}

      {tab === "runtime" && (
        <RuntimeTab
          runtime={runtime}
          envResult={envResult}
          onSelect={selectRuntime}
          onRefresh={refreshEnvironment}
        />
      )}
      {tab === "graph" && (
        <GraphTab snapshot={snapshot} result={graphResult} onRefresh={refreshGraph} />
      )}
      {tab === "topics" && <TopicsTab snapshot={snapshot} onRefresh={refreshGraph} />}
      {tab === "tf" && <TfTab result={tfResult} onRefresh={refreshTf} />}
      {tab === "diagnostics" && (
        <DiagnosticsTab result={diagResult} onRefresh={refreshDiagnostics} />
      )}
      {tab === "lifecycle" && (
        <LifecycleTab result={lifecycleResult} onRefresh={refreshLifecycle} />
      )}
    </div>
  );
}

function RuntimeTab({
  runtime,
  envResult,
  onSelect,
  onRefresh,
}: {
  runtime: RosRuntimeConfig | null;
  envResult: CheckResult | null;
  onSelect: (c: RosRuntimeConfig) => void;
  onRefresh: () => void;
}) {
  const discovered = discoveredRuntimes(envResult);
  const info = environmentInfo(envResult);
  return (
    <>
      <div className="card">
        <div className="card-title-row">
          <h3>Active runtime</h3>
          <button className="btn btn-small" onClick={onRefresh}>
            Refresh
          </button>
        </div>
        {info ? (
          <table className="table table-plain">
            <tbody>
              <tr><td className="obs-key">Distribution</td><td>{String(info.distro ?? "–")}</td></tr>
              <tr><td className="obs-key">Provider</td><td>{String(info.provider ?? "none")}</td></tr>
              <tr><td className="obs-key">Domain ID</td><td>{String(info.domain_id ?? "–")}</td></tr>
              <tr><td className="obs-key">RMW</td><td>{String(info.rmw_implementation ?? "–")}</td></tr>
              <tr><td className="obs-key">Python</td><td className="mono">{String(info.python_executable ?? "–")}{info.python_version ? ` (${info.python_version})` : ""}</td></tr>
              <tr><td className="obs-key">rclpy / CLI</td><td>{info.rclpy_available ? "rclpy ✓" : "rclpy ✗"} · {info.cli_available ? "cli ✓" : "cli ✗"}</td></tr>
              <tr><td className="obs-key">Setup chain</td><td className="mono">{runtime?.setup_scripts?.join(" → ") || "(inherited)"}</td></tr>
              <tr><td className="obs-key">Overlays</td><td className="mono">{Array.isArray(info.overlays) && info.overlays.length ? (info.overlays as string[]).join(", ") : "–"}</td></tr>
            </tbody>
          </table>
        ) : (
          <p className="muted">No environment observation yet.</p>
        )}
      </div>

      <div className="card">
        <h3>Discovered runtimes</h3>
        {discovered.length === 0 ? (
          <p className="muted">
            No ROS runtimes discovered (inherited environment, /opt/ros, well-known
            Windows locations).
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Mode</th><th>Setup</th><th></th></tr>
            </thead>
            <tbody>
              {discovered.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>{c.mode}</td>
                  <td className="mono">{c.setup_scripts.join(" → ") || "(inherited)"}</td>
                  <td>
                    {runtime?.id === c.id ? (
                      <span className="badge badge-ok">SELECTED</span>
                    ) : (
                      <button className="btn btn-small" onClick={() => onSelect(c)}>
                        Select
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {envResult && envResult.evidence.length > 0 && (
        <div className="card">
          <h3>Evidence</h3>
          {envResult.evidence.map((e) => (
            <EvidenceView key={e.id} evidence={e} />
          ))}
        </div>
      )}
    </>
  );
}

function GraphTab({
  snapshot,
  result,
  onRefresh,
}: {
  snapshot: RosGraphSnapshot | null;
  result: CheckResult | null;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  return (
    <>
      <div className="card-title-row" style={{ marginBottom: 10 }}>
        <input
          className="targets-input mono"
          style={{ margin: 0, maxWidth: 320 }}
          placeholder="filter…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="btn btn-small" onClick={onRefresh}>
          Refresh graph
        </button>
      </div>
      {!snapshot ? (
        <p className="muted">No graph snapshot yet — press Refresh graph.</p>
      ) : (
        <>
          <p className="muted">
            provider {snapshot.provider} · discovery {snapshot.discovery_ms} ms ·{" "}
            {snapshot.nodes.length} nodes · {snapshot.topics.length} topics
          </p>
          <div className="card">
            <h3>Nodes</h3>
            <table className="table">
              <thead>
                <tr><th>Name</th><th>Namespace</th><th>Pubs</th><th>Subs</th><th>Services</th></tr>
              </thead>
              <tbody>
                {filterBy(snapshot.nodes, query, (n) => n.full_name).map((n) => (
                  <tr key={n.full_name}>
                    <td className="mono">{n.full_name}</td>
                    <td className="mono">{n.namespace}</td>
                    <td>{n.publishers.length}</td>
                    <td>{n.subscriptions.length}</td>
                    <td>{n.services.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Services</h3>
            <table className="table">
              <thead><tr><th>Name</th><th>Type</th></tr></thead>
              <tbody>
                {filterBy(snapshot.services, query, (s) => s.name).map((s) => (
                  <tr key={s.name}>
                    <td className="mono">{s.name}</td>
                    <td className="mono">{s.types.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Actions</h3>
            {snapshot.actions.length === 0 ? (
              <p className="muted">No actions observed.</p>
            ) : (
              <table className="table">
                <thead><tr><th>Name</th><th>Type</th><th>Servers</th></tr></thead>
                <tbody>
                  {filterBy(snapshot.actions, query, (a) => a.name).map((a) => (
                    <tr key={a.name}>
                      <td className="mono">{a.name}</td>
                      <td className="mono">{a.types.join(", ")}</td>
                      <td className="mono">{a.servers.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {result && result.evidence.length > 0 && (
            <div className="card">
              <h3>Evidence</h3>
              {result.evidence.map((e) => (
                <EvidenceView key={e.id} evidence={e} />
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function TopicsTab({
  snapshot,
  onRefresh,
}: {
  snapshot: RosGraphSnapshot | null;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<RosTopicInfo | null>(null);
  const [sample, setSample] = useState<CheckResult | null>(null);
  const [sampling, setSampling] = useState(false);

  const sampleTopic = (topic: string) => {
    setSampling(true);
    setSample(null);
    api
      .runSingleCheck("ros.topic_rate", { topics: [topic], duration_s: 3.0 })
      .then(setSample)
      .catch(() => {})
      .finally(() => setSampling(false));
  };

  if (!snapshot) {
    return (
      <p className="muted">
        No graph snapshot yet —{" "}
        <button className="btn btn-small" onClick={onRefresh}>
          Refresh graph
        </button>
      </p>
    );
  }
  if (selected) {
    const sampleData = sample
      ? (sample.observations.find((o) => o.key === `ros.topic_rate.${selected.name}`)?.value as
          | { type: "JSON"; value: Record<string, unknown> }
          | undefined)
      : undefined;
    return (
      <>
        <div className="card-title-row" style={{ marginBottom: 10 }}>
          <h3 className="mono">{selected.name}</h3>
          <div className="actions">
            <button
              className="btn btn-primary btn-small"
              disabled={sampling}
              onClick={() => sampleTopic(selected.name)}
            >
              {sampling ? "Sampling (3 s)…" : "Sample topic"}
            </button>
            <button className="btn btn-small" onClick={() => setSelected(null)}>
              ← All topics
            </button>
          </div>
        </div>
        <div className="card">
          <table className="table table-plain">
            <tbody>
              <tr><td className="obs-key">Type</td><td className="mono">{selected.types.join(", ")}</td></tr>
              <tr><td className="obs-key">Publishers</td><td>{selected.publisher_count}</td></tr>
              <tr><td className="obs-key">Subscribers</td><td>{selected.subscriber_count}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Endpoints</h3>
          <table className="table">
            <thead><tr><th>Node</th><th>Role</th><th>QoS</th></tr></thead>
            <tbody>
              {[...selected.publishers, ...selected.subscribers].map((ep, i) => (
                <tr key={i}>
                  <td className="mono">{ep.node}</td>
                  <td>{ep.endpoint_type}</td>
                  <td className="mono">
                    {String(ep.qos["reliability"] ?? "?")} / {String(ep.qos["durability"] ?? "?")}
                    {ep.qos["depth"] != null ? ` / depth ${ep.qos["depth"]}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {sample && (
          <div className="card">
            <div className="card-title-row">
              <h3>Sampling result</h3>
              <StatusChip status={sample.status} />
            </div>
            {sampleData ? (
              <pre className="evidence-data">{JSON.stringify(sampleData.value, null, 2)}</pre>
            ) : (
              sample.error && <p className="check-error">{sample.error.message}</p>
            )}
            {sample.evidence.map((e) => (
              <EvidenceView key={e.id} evidence={e} />
            ))}
          </div>
        )}
      </>
    );
  }
  return (
    <>
      <div className="card-title-row" style={{ marginBottom: 10 }}>
        <input
          className="targets-input mono"
          style={{ margin: 0, maxWidth: 320 }}
          placeholder="filter topics…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="btn btn-small" onClick={onRefresh}>
          Refresh graph
        </button>
      </div>
      <table className="table">
        <thead>
          <tr><th>Topic</th><th>Type</th><th>Pubs</th><th>Subs</th></tr>
        </thead>
        <tbody>
          {filterBy(snapshot.topics, query, (t) => t.name).map((t) => (
            <tr key={t.name} className="row-link" onClick={() => setSelected(t)}>
              <td className="mono">{t.name}</td>
              <td className="mono">{t.types.join(", ")}</td>
              <td>{t.publisher_count}</td>
              <td>{t.subscriber_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function TfTab({ result, onRefresh }: { result: CheckResult | null; onRefresh: () => void }) {
  const [source, setSource] = useState("map");
  const [target, setTarget] = useState("base_link");
  const [queryResult, setQueryResult] = useState<CheckResult | null>(null);
  const [querying, setQuerying] = useState(false);
  const snapshot = tfSnapshot(result);

  const runQuery = () => {
    setQuerying(true);
    api
      .runSingleCheck("ros.tf", {
        listen_s: 1.0,
        tf_queries: [{ source, target, timeout_s: 2.0 }],
      })
      .then(setQueryResult)
      .catch(() => {})
      .finally(() => setQuerying(false));
  };

  const queryObs = queryResult?.observations.find((o) =>
    o.key.startsWith("ros.tf.query."),
  );
  const queryData =
    queryObs && queryObs.value.type === "JSON"
      ? (queryObs.value.value as Record<string, unknown>)
      : null;

  return (
    <>
      <div className="card">
        <div className="card-title-row">
          <h3>Transform query</h3>
          <button className="btn btn-small" onClick={onRefresh}>
            Refresh TF snapshot
          </button>
        </div>
        <div className="actions" style={{ marginTop: 8 }}>
          <input
            className="targets-input mono"
            style={{ margin: 0, maxWidth: 180 }}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="source frame"
          />
          <span className="muted">→</span>
          <input
            className="targets-input mono"
            style={{ margin: 0, maxWidth: 180 }}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="target frame"
          />
          <button className="btn btn-primary btn-small" disabled={querying} onClick={runQuery}>
            {querying ? "Querying…" : "Can transform?"}
          </button>
        </div>
        {queryData && (
          <p style={{ marginTop: 10 }}>
            <span
              className={`badge ${queryData.status === "AVAILABLE" ? "badge-ok" : "badge-warn"}`}
            >
              {String(queryData.status)}
            </span>{" "}
            <span className="muted">
              {queryData.elapsed_ms != null ? `${Number(queryData.elapsed_ms).toFixed(0)} ms` : ""}
              {queryData.reason ? ` · ${queryData.reason}` : ""}
            </span>
          </p>
        )}
      </div>

      {snapshot ? (
        <>
          <div className="card">
            <h3>Frames ({snapshot.frames.length})</h3>
            <p className="mono muted">{snapshot.frames.join(", ") || "none observed"}</p>
          </div>
          <div className="card">
            <h3>Relationships</h3>
            <table className="table">
              <thead><tr><th>Parent</th><th>Child</th><th>Static</th><th>Last stamp</th></tr></thead>
              <tbody>
                {snapshot.edges.map((e, i) => (
                  <tr key={i}>
                    <td className="mono">{e.parent}</td>
                    <td className="mono">{e.child}</td>
                    <td>{e.is_static == null ? "?" : e.is_static ? "static" : "dynamic"}</td>
                    <td>{e.last_stamp != null ? e.last_stamp.toFixed(2) : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Connected components ({snapshot.connected_components.length})</h3>
            {snapshot.connected_components.map((c, i) => (
              <p key={i} className="mono muted">
                {c.join(" ↔ ")}
              </p>
            ))}
          </div>
        </>
      ) : (
        <p className="muted">No TF snapshot yet — press Refresh TF snapshot.</p>
      )}
    </>
  );
}

function DiagnosticsTab({
  result,
  onRefresh,
}: {
  result: CheckResult | null;
  onRefresh: () => void;
}) {
  const [levelFilter, setLevelFilter] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const statuses = diagnosticStatuses(result).filter(
    (s) =>
      (levelFilter === null || s.level === levelFilter) &&
      (s.name + s.hardware_id).toLowerCase().includes(query.toLowerCase()),
  );
  const levelClass = (level: number) =>
    level === 0 ? "badge badge-ok" : level === 1 ? "badge badge-warn" : level === 2 ? "badge badge-crit" : "badge badge-muted";
  return (
    <>
      <div className="card-title-row" style={{ marginBottom: 10 }}>
        <div className="actions">
          {[null, 0, 1, 2, 3].map((level) => (
            <button
              key={String(level)}
              className={`btn btn-small ${levelFilter === level ? "btn-primary" : ""}`}
              onClick={() => setLevelFilter(level as number | null)}
            >
              {level === null ? "ALL" : DIAG_LEVEL_LABELS[level as number]}
            </button>
          ))}
          <input
            className="targets-input mono"
            style={{ margin: 0, maxWidth: 220 }}
            placeholder="component / hardware_id…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <button className="btn btn-small" onClick={onRefresh}>
          Collect (2 s)
        </button>
      </div>
      {statuses.length === 0 ? (
        <p className="muted">
          {result ? "No diagnostic statuses match." : "No collection yet — press Collect."}
        </p>
      ) : (
        statuses.map((s) => (
          <div key={`${s.source_topic}:${s.name}`} className="card">
            <div className="finding-head">
              <span className={levelClass(s.level)}>{DIAG_LEVEL_LABELS[s.level] ?? s.level}</span>
              <strong>{s.name}</strong>
              <span className="muted mono">{s.hardware_id}</span>
              <span className="muted">{s.source_topic}</span>
            </div>
            <p>{s.message}</p>
            {Object.keys(s.values).length > 0 && (
              <table className="obs-table">
                <tbody>
                  {Object.entries(s.values).map(([k, v]) => (
                    <tr key={k}>
                      <td className="obs-key">{k}</td>
                      <td>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))
      )}
    </>
  );
}

function LifecycleTab({
  result,
  onRefresh,
}: {
  result: CheckResult | null;
  onRefresh: () => void;
}) {
  const states = lifecycleStates(result);
  return (
    <>
      <div className="card-title-row" style={{ marginBottom: 10 }}>
        <p className="muted">Read-only: lifecycle states are observed, never transitioned.</p>
        <button className="btn btn-small" onClick={onRefresh}>
          Inspect
        </button>
      </div>
      {states.length === 0 ? (
        <p className="muted">
          {result
            ? "No lifecycle-managed nodes observed (plain nodes are not errors)."
            : "No inspection yet — press Inspect."}
        </p>
      ) : (
        <table className="table">
          <thead><tr><th>Node</th><th>State</th><th>Services</th></tr></thead>
          <tbody>
            {states.map((s, i) => (
              <tr key={i}>
                <td className="mono">{String(s.node)}</td>
                <td>{String(s.state_label)} ({String(s.state_id)})</td>
                <td>{s.services_available ? "reachable" : "unreachable"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
