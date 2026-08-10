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
  PluginSummary,
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

export function listRuns(): Promise<CheckRun[]> {
  return invoke("list_runs");
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
