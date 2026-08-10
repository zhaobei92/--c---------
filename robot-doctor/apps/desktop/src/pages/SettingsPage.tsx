import type { AppInfo } from "../types";

interface Props {
  info: AppInfo | null;
}

export function SettingsPage({ info }: Props) {
  return (
    <div className="page">
      <header className="page-header">
        <h1>Settings</h1>
      </header>
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
          </tbody>
        </table>
      </div>
    </div>
  );
}
