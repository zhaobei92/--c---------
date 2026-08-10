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
            <th>API</th>
            <th>Checks</th>
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
              <td>{p.api_version || "–"}</td>
              <td>{p.check_count}</td>
              <td>{p.restarts}</td>
              <td>
                {p.error ? (
                  <span className="badge badge-crit" title={p.error}>
                    LOAD FAILED
                  </span>
                ) : (
                  <span className="badge badge-ok">LOADED</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {plugins.some((p) => p.error) && (
        <div className="card">
          <h3>Load errors</h3>
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
        Plugins are separate processes speaking the JSONL plugin protocol. A
        crashing plugin is restarted automatically and can never take Robot
        Doctor down.
      </p>
    </div>
  );
}
