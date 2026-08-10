import type { Device } from "../types";
import { HealthBadge } from "../components/badges";

interface Props {
  devices: Device[];
}

export function DevicesPage({ devices }: Props) {
  return (
    <div className="page">
      <header className="page-header">
        <h1>Devices</h1>
        <div className="actions">
          <button
            className="btn"
            disabled
            title="Remote devices connect through the Robot Doctor agent over gRPC (Phase E)."
          >
            Add device
          </button>
        </div>
      </header>

      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Endpoint</th>
            <th>Connection</th>
            <th>Health</th>
            <th>Capabilities</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => (
            <tr key={d.id}>
              <td>{d.name}</td>
              <td>
                {d.endpoint.type === "LOCAL"
                  ? "local (embedded core)"
                  : `${d.endpoint.host}:${d.endpoint.port}`}
              </td>
              <td>{d.connection}</td>
              <td>
                <HealthBadge health={d.health} />
              </td>
              <td className="muted">
                {d.capabilities.length > 0
                  ? d.capabilities.map((c) => c.name).join(", ")
                  : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Remote robots and workstations are added here once the Robot Doctor agent
        (gRPC) ships; the local machine is always available.
      </p>
    </div>
  );
}
