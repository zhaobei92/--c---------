// Typed boundary to the Tauri backend. Every page talks to the real core
// through these calls — there is no mock data path.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  AppInfo,
  Baseline,
  BaselineDiff,
  BaselineSummaryRow,
  C2ErrorPayload,
  CaptureCandidate,
  CheckResult,
  CheckRun,
  ComparisonView,
  Device,
  DeviceProfileAssignment,
  DiagnosisEvent,
  DiagnosticMode,
  DraftReport,
  EvaluationRun,
  NetworkTarget,
  PluginSummary,
  Profile,
  ProfileRevisionRow,
  ProfileRow,
  ProfileStatus,
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

export function getRosRuntime(): Promise<unknown | null> {
  return invoke("get_ros_runtime");
}

export function setRosRuntime(runtime: unknown): Promise<void> {
  return invoke("set_ros_runtime", { runtime });
}

/** Run one check on demand (SAMPLE TOPIC, TF query, discovery, refresh). */
export function runSingleCheck(checkId: string, params: unknown): Promise<CheckResult> {
  return invoke("run_single_check", { checkId, params });
}

/** Subscribe to progressive diagnosis events emitted by the backend. */
export function onDiagnosisEvent(
  handler: (event: DiagnosisEvent) => void,
): Promise<UnlistenFn> {
  return listen<DiagnosisEvent>("diagnosis-event", (e) => handler(e.payload));
}

// ── Phase C2: baselines, profiles, evaluation ─────────────────────────

/**
 * Backend C2 errors arrive as a JSON payload so validation failures can
 * be shown against the field they belong to. Anything else is surfaced
 * verbatim rather than swallowed.
 */
export function parseC2Error(err: unknown): C2ErrorPayload {
  const text = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(text) as C2ErrorPayload;
    if (typeof parsed?.message === "string") {
      return { message: parsed.message, validation_errors: parsed.validation_errors ?? [] };
    }
  } catch {
    // Not a structured payload — fall through.
  }
  return { message: text, validation_errors: [] };
}

export function listBaselineCandidates(limit?: number): Promise<CaptureCandidate[]> {
  return invoke("list_baseline_candidates", { limit });
}

export function captureBaseline(
  name: string,
  description: string,
  runIds: string[],
  manuallyAccepted: boolean,
): Promise<Baseline> {
  return invoke("capture_baseline", {
    name,
    description,
    runIds,
    manuallyAccepted,
  });
}

export function listBaselines(): Promise<BaselineSummaryRow[]> {
  return invoke("list_baselines");
}

export function getBaseline(baselineId: string): Promise<Baseline> {
  return invoke("get_baseline", { baselineId });
}

export function updateBaseline(
  baselineId: string,
  name: string,
  description: string,
  tags: string[],
): Promise<boolean> {
  return invoke("update_baseline", { baselineId, name, description, tags });
}

export function deleteBaseline(baselineId: string): Promise<boolean> {
  return invoke("delete_baseline", { baselineId });
}

export function diffRunAgainstBaseline(baselineId: string, runId: string): Promise<BaselineDiff> {
  return invoke("diff_run_against_baseline", { baselineId, runId });
}

export function draftProfile(baselineId: string): Promise<DraftReport> {
  return invoke("draft_profile", { baselineId });
}

export function saveProfile(profile: Profile): Promise<Profile> {
  return invoke("save_profile", { profile });
}

export function importProfile(yaml: string, profileId?: string): Promise<Profile> {
  return invoke("import_profile", { yaml, profileId });
}

export function exportProfile(profileId: string, revision: number): Promise<string> {
  return invoke("export_profile", { profileId, revision });
}

export function listProfiles(): Promise<ProfileRow[]> {
  return invoke("list_profiles");
}

export function listProfileRevisions(profileId: string): Promise<ProfileRevisionRow[]> {
  return invoke("list_profile_revisions", { profileId });
}

export function getProfile(profileId: string, revision: number): Promise<Profile> {
  return invoke("get_profile", { profileId, revision });
}

export function setProfileStatus(
  profileId: string,
  revision: number,
  status: ProfileStatus,
): Promise<boolean> {
  return invoke("set_profile_status", { profileId, revision, status });
}

export function deleteProfileRevision(profileId: string, revision: number): Promise<boolean> {
  return invoke("delete_profile_revision", { profileId, revision });
}

export function activateProfile(
  profileId: string,
  revision: number,
  runtimeId?: string,
): Promise<DeviceProfileAssignment> {
  return invoke("activate_profile", { profileId, revision, runtimeId });
}

export function getActiveProfile(): Promise<DeviceProfileAssignment | null> {
  return invoke("get_active_profile");
}

export function getEvaluation(runId: string): Promise<EvaluationRun | null> {
  return invoke("get_evaluation", { runId });
}

/** Re-evaluate a stored run against the active profile. */
export function evaluateRun(runId: string): Promise<EvaluationRun | null> {
  return invoke("evaluate_run", { runId });
}

export function getComparison(runId: string, baselineId?: string): Promise<ComparisonView> {
  return invoke("get_comparison", { runId, baselineId });
}
