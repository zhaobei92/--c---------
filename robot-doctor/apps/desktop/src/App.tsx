import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { applyEvent, type RunView } from "./diagnosisStore";
import { DevicesPage } from "./pages/DevicesPage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PluginsPage } from "./pages/PluginsPage";
import { SettingsPage } from "./pages/SettingsPage";
import type { AppInfo, Device, DiagnosticMode, PluginSummary } from "./types";

type Page = "overview" | "devices" | "diagnostics" | "plugins" | "settings";

const NAV: { id: Page; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "devices", label: "Devices" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "plugins", label: "Plugins" },
  { id: "settings", label: "Settings" },
];

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [devices, setDevices] = useState<Device[]>([]);
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [run, setRun] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshDevices = useCallback(() => {
    api.listDevices().then(setDevices).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    refreshDevices();
    api.listPlugins().then(setPlugins).catch((e) => setError(String(e)));
    api.getAppInfo().then(setInfo).catch((e) => setError(String(e)));

    let unlisten: (() => void) | undefined;
    api
      .onDiagnosisEvent((event) => {
        setRun((current) => applyEvent(current, event));
        if (event.type === "RUN_COMPLETED") {
          // Health on the device list reflects the finished run.
          refreshDevices();
        }
      })
      .then((fn) => {
        unlisten = fn;
      })
      .catch((e) => setError(String(e)));
    return () => unlisten?.();
  }, [refreshDevices]);

  const startDiagnosis = useCallback(
    (mode: DiagnosticMode) => {
      setError(null);
      api.runDiagnosis("local", mode).catch((e) => setError(String(e)));
      setPage("diagnostics");
    },
    [],
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">
          <span className="logo-mark">☤</span> Robot Doctor
        </div>
        <nav>
          {NAV.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "nav-item-active" : ""}`}
              onClick={() => setPage(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">{info ? `v${info.version}` : ""}</div>
      </aside>

      <main className="content">
        {error && <div className="error-banner">{error}</div>}
        {page === "overview" && (
          <OverviewPage
            devices={devices}
            run={run}
            onRunDiagnosis={startDiagnosis}
            onOpenDiagnostics={() => setPage("diagnostics")}
          />
        )}
        {page === "devices" && <DevicesPage devices={devices} />}
        {page === "diagnostics" && <DiagnosticsPage run={run} onRunDiagnosis={startDiagnosis} />}
        {page === "plugins" && <PluginsPage plugins={plugins} />}
        {page === "settings" && <SettingsPage info={info} />}
      </main>
    </div>
  );
}
