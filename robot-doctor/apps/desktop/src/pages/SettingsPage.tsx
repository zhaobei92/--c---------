import { useEffect, useState } from "react";
import * as api from "../api";
import type { AppInfo, NetworkTarget } from "../types";

interface Props {
  info: AppInfo | null;
}

/** Parse one target per line: `host`, `host:port` or `host:port timeout_ms`. */
export function parseTargets(text: string): NetworkTarget[] {
  const targets: NetworkTarget[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const [addr, timeoutStr] = line.split(/\s+/);
    const colon = addr.lastIndexOf(":");
    let host = addr;
    let port: number | undefined;
    if (colon > 0) {
      const maybePort = Number(addr.slice(colon + 1));
      if (Number.isInteger(maybePort) && maybePort > 0 && maybePort <= 65535) {
        host = addr.slice(0, colon);
        port = maybePort;
      }
    }
    const timeout = Number(timeoutStr);
    targets.push({
      host,
      ...(port !== undefined ? { port } : {}),
      ...(Number.isFinite(timeout) && timeout > 0 ? { timeout_ms: timeout } : {}),
    });
  }
  return targets;
}

export function formatTargets(targets: NetworkTarget[]): string {
  return targets
    .map((t) => {
      const addr = t.port !== undefined ? `${t.host}:${t.port}` : t.host;
      return t.timeout_ms !== undefined ? `${addr} ${t.timeout_ms}` : addr;
    })
    .join("\n");
}

export function SettingsPage({ info }: Props) {
  const [targetsText, setTargetsText] = useState("");
  const [saveState, setSaveState] = useState<string | null>(null);

  useEffect(() => {
    api
      .getNetworkTargets()
      .then((targets) => setTargetsText(formatTargets(targets)))
      .catch(() => {});
  }, []);

  const save = () => {
    const targets = parseTargets(targetsText);
    api
      .setNetworkTargets(targets)
      .then(() => setSaveState(`Saved ${targets.length} target(s).`))
      .catch((e) => setSaveState(String(e)));
  };

  return (
    <div className="page">
      <header className="page-header">
        <h1>Settings</h1>
      </header>

      <div className="card">
        <h3>Network targets</h3>
        <p className="muted">
          Hosts the network diagnostics should reach. One per line:
          <code className="mono"> host</code>, <code className="mono">host:port</code> or
          <code className="mono"> host:port timeout_ms</code>. Used by
          reachability, TCP-port and latency checks. Device profiles will
          supply these per-robot later.
        </p>
        <textarea
          className="targets-input mono"
          rows={6}
          placeholder={"192.168.1.1\nrobot.local:22 2000"}
          value={targetsText}
          onChange={(e) => setTargetsText(e.target.value)}
        />
        <div className="actions">
          <button className="btn btn-primary btn-small" onClick={save}>
            Save targets
          </button>
          {saveState && <span className="muted">{saveState}</span>}
        </div>
      </div>

      <div className="card">
        <h3>Application</h3>
        <table className="table table-plain">
          <tbody>
            <tr>
              <td className="obs-key">Version</td>
              <td>{info?.version ?? "–"}</td>
            </tr>
            <tr>
              <td className="obs-key">Platform</td>
              <td>{info?.platform ?? "–"}</td>
            </tr>
            <tr>
              <td className="obs-key">Plugins directory</td>
              <td className="mono">{info?.plugins_dir ?? "–"}</td>
            </tr>
            <tr>
              <td className="obs-key">History database</td>
              <td className="mono">
                {info?.database_path ?? "–"}
                {info && !info.storage_ok && (
                  <span className="badge badge-crit" style={{ marginLeft: 8 }}>
                    UNAVAILABLE
                  </span>
                )}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
