// Typed boundary to the Tauri backend. Every page talks to the real core
// through these calls — there is no mock data path.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  AppInfo,
  CheckRun,
  Device,
  DiagnosisEvent,
  DiagnosticMode,
  NetworkTarget,
  PluginSummary,
  RunFilter,
  RunSummaryRow,
  StoredRun,
} from "./types";

export function listDevices(): Promise<Device[]> {
  return invoke("list_devices");
}

export function listPlugins(): Promise<PluginSummary[]> {
  return invoke("list_plugins");
}

export function runDiagnosis(deviceId: string, mode: DiagnosticMode): Promise<string> {
  return invoke("run_diagnosis", { deviceId, mode });
}

export function getRun(runId: string): Promise<CheckRun | null> {
  return invoke("get_run", { runId });
}

export function cancelDiagnosis(runId: string): Promise<void> {
  return invoke("cancel_diagnosis", { runId });
}

export function listHistory(filter: RunFilter): Promise<RunSummaryRow[]> {
  return invoke("list_history", { filter });
}

export function getHistoryRun(runId: string): Promise<StoredRun | null> {
  return invoke("get_history_run", { runId });
}

export function deleteHistoryRun(runId: string): Promise<boolean> {
  return invoke("delete_history_run", { runId });
}

export function getNetworkTargets(): Promise<NetworkTarget[]> {
  return invoke("get_network_targets");
}

export function setNetworkTargets(targets: NetworkTarget[]): Promise<void> {
  return invoke("set_network_targets", { targets });
}

export function getAppInfo(): Promise<AppInfo> {
  return invoke("get_app_info");
}

/** Subscribe to progressive diagnosis events emitted by the backend. */
export function onDiagnosisEvent(
  handler: (event: DiagnosisEvent) => void,
): Promise<UnlistenFn> {
  return listen<DiagnosisEvent>("diagnosis-event", (e) => handler(e.payload));
}
