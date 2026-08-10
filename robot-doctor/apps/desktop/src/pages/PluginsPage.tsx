import type { PluginSummary } from "../types";

interface Props {
  plugins: PluginSummary[];
}

export function PluginsPage({ plugins }: Props) {
  return (
    <div className="page">
      <header className="page-header">
        <h1>Plugins</h1>
      </header>
      <table className="table">
        <thead>
          <tr>
            <th>Plugin</th>
            <th>Version</th>
            <th>Capabilities</th>
            <th>Checks</th>
            <th>Concurrency</th>
            <th>Restarts</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {plugins.map((p) => (
            <tr key={p.id}>
              <td>
                {p.name}
                <span className="check-id">{p.id}</span>
              </td>
              <td>{p.version || "–"}</td>
              <td className="muted">
                {p.contacted ? p.capabilities.join(", ") || "–" : "not contacted yet"}
              </td>
              <td>{p.contacted ? p.checks.length : "–"}</td>
              <td>{p.contacted ? p.max_concurrency : "–"}</td>
              <td>{p.restarts}</td>
              <td>
                {p.error ? (
                  <span className="badge badge-crit" title={p.error}>
                    ERROR
                  </span>
                ) : p.contacted ? (
                  <span className="badge badge-ok">READY</span>
                ) : (
                  <span className="badge badge-muted">DISCOVERED</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {plugins.some((p) => p.error) && (
        <div className="card">
          <h3>Errors</h3>
          {plugins
            .filter((p) => p.error)
            .map((p) => (
              <p key={p.id} className="check-error">
                {p.name}: {p.error}
              </p>
            ))}
        </div>
      )}
      <p className="muted">
        Capabilities and checks are discovered from the running plugin process
        (CAPABILITIES negotiation) — the manifest only bootstraps the process.
        A crashing plugin is restarted automatically and can never take Robot
        Doctor down.
      </p>
    </div>
  );
}
