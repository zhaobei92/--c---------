const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface DecisionMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface DecisionSummary {
  id: string;
  title: string;
  domain: string;
  risk_level: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface DecisionOption {
  id: string;
  name: string;
  description: string | null;
  is_eligible: boolean;
  elimination_reason: string | null;
  source: string;
}

export interface HardConstraint {
  id: string;
  description: string;
  is_hard: boolean;
  source: string;
}

export interface Criterion {
  id: string;
  name: string;
  criterion_type: string;
  direction: string;
  utility_curve_type: string;
  initial_weight: number;
  learned_weight: number | null;
  weight_uncertainty: number | null;
  source: string;
}

export interface DecisionDetail extends DecisionSummary {
  facts: string[];
  concerns: string[];
  unknowns: string[];
  options: DecisionOption[];
  constraints: HardConstraint[];
  criteria: Criterion[];
  messages: DecisionMessage[];
}

export interface AdvanceResult {
  kind: string;
  status: string;
  message: string | null;
  diagnosis: {
    primary_type: string;
    explanation_summary: string;
  } | null;
  question: { question: string; target_variable: string } | null;
}

export interface NextComparison {
  done: boolean;
  left: Criterion | null;
  right: Criterion | null;
  prompt: string | null;
  comparisons_done: number;
  comparisons_required: number;
}

export interface Evaluation {
  id: string;
  option_id: string;
  criterion_id: string;
  expected_value: number;
  uncertainty: number;
}

export interface DecisionRun {
  id: string;
  algorithm_version: string;
  seed: number;
  ranking_stability: number | null;
  winning_option_id: string | null;
  result_snapshot: {
    winner: string | null;
    ranking: string[];
    winner_probability: Record<string, number>;
    expected_utility_gap: number;
    deterministic_utilities: Record<string, number>;
    minimax_regret_option: string | null;
    critical_variables: string[];
    sensitivity_flips: { variable: string; change: string; new_winner: string }[];
    eliminated: { option_key: string; reason: string }[];
    aspiration_warnings: string[];
  };
}

export interface Recommendation {
  id: string;
  recommended_option_id: string;
  summary: string;
  main_reasons: string[];
  accepted_tradeoffs: string[];
  critical_unknowns: string[];
  reopen_conditions: string[];
  non_reopen_conditions: string[];
  next_action: string;
  challenger_output: {
    missing_assumptions: string[];
    fragile_variables: string[];
    counterargument: string;
  } | null;
  source: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!resp.ok) {
    throw new Error(`API ${resp.status}: ${await resp.text()}`);
  }
  return resp.json() as Promise<T>;
}

export function createDecision(input: string) {
  return request<{ decision_id: string; status: string; stream_url: string }>(
    "/api/v1/decisions",
    { method: "POST", body: JSON.stringify({ input, input_type: "text" }) },
  );
}

export function getDecision(id: string) {
  return request<DecisionDetail>(`/api/v1/decisions/${id}`);
}

export function listDecisions() {
  return request<DecisionSummary[]>("/api/v1/decisions");
}

export function sendMessage(id: string, content: string) {
  return request<{
    user_message: DecisionMessage;
    assistant_message: DecisionMessage;
    status: string;
  }>(`/api/v1/decisions/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function streamUrl(id: string) {
  return `${API_BASE}/api/v1/decisions/${id}/stream`;
}

export function analyzeDecision(id: string) {
  return request<DecisionDetail>(`/api/v1/decisions/${id}/analyze`, {
    method: "POST",
  });
}

export function updateOption(
  decisionId: string,
  optionId: string,
  patch: Partial<Pick<DecisionOption, "name" | "description">>,
) {
  return request<DecisionOption>(
    `/api/v1/decisions/${decisionId}/options/${optionId}`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
}

export function addOption(decisionId: string, name: string) {
  return request<DecisionOption>(`/api/v1/decisions/${decisionId}/options`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function deleteOption(decisionId: string, optionId: string) {
  return fetch(`${API_BASE}/api/v1/decisions/${decisionId}/options/${optionId}`, {
    method: "DELETE",
  });
}

export function addConstraint(
  decisionId: string,
  description: string,
  isHard: boolean,
) {
  return request<HardConstraint>(
    `/api/v1/decisions/${decisionId}/constraints`,
    {
      method: "POST",
      body: JSON.stringify({ description, is_hard: isHard }),
    },
  );
}

export function deleteConstraint(decisionId: string, constraintId: string) {
  return fetch(
    `${API_BASE}/api/v1/decisions/${decisionId}/constraints/${constraintId}`,
    { method: "DELETE" },
  );
}

export function advanceDecision(id: string) {
  return request<AdvanceResult>(`/api/v1/decisions/${id}/advance`, {
    method: "POST",
  });
}

export function getNextComparison(id: string) {
  return request<NextComparison>(`/api/v1/decisions/${id}/comparisons/next`);
}

export function submitComparison(
  id: string,
  leftId: string,
  rightId: string,
  choice: "left" | "right" | "equal" | "incomparable",
  strength: number,
) {
  return request(`/api/v1/decisions/${id}/comparisons`, {
    method: "POST",
    body: JSON.stringify({
      left_criterion_id: leftId,
      right_criterion_id: rightId,
      choice,
      strength,
    }),
  });
}

export function getEvaluations(id: string) {
  return request<Evaluation[]>(`/api/v1/decisions/${id}/evaluations`);
}

export function putEvaluations(
  id: string,
  items: {
    option_id: string;
    criterion_id: string;
    expected_value: number;
    uncertainty?: number;
  }[],
) {
  return request<Evaluation[]>(`/api/v1/decisions/${id}/evaluations`, {
    method: "PUT",
    body: JSON.stringify({ evaluations: items }),
  });
}

export function computeDecision(id: string) {
  return request<DecisionRun>(`/api/v1/decisions/${id}/compute`, {
    method: "POST",
  });
}

export function createRecommendation(id: string) {
  return request<Recommendation>(`/api/v1/decisions/${id}/recommendation`, {
    method: "POST",
  });
}

export function getRecommendation(id: string) {
  return request<Recommendation>(`/api/v1/decisions/${id}/recommendation`);
}

export function getLatestRun(id: string) {
  return request<DecisionRun>(`/api/v1/decisions/${id}/runs/latest`);
}

export interface ClosureContract {
  id: string;
  selected_option_id: string;
  accepted_tradeoffs: string[];
  main_reasons: string[];
  reopen_conditions: string[];
  non_reopen_conditions: string[];
  next_action: string;
  followed_recommendation: boolean;
  user_confirmed: boolean;
}

export interface ReopenResult {
  outcome: string;
  reopen_score: number;
  novelty: number;
  is_rumination: boolean;
  message: string;
  status: string;
}

export function commitDecision(id: string, selectedOptionId: string) {
  return request<ClosureContract>(`/api/v1/decisions/${id}/commit`, {
    method: "POST",
    body: JSON.stringify({
      selected_option_id: selectedOptionId,
      accepted_tradeoffs: true,
    }),
  });
}

export function getContract(id: string) {
  return request<ClosureContract>(`/api/v1/decisions/${id}/contract`);
}

export function reopenDecision(id: string, newInformation: string) {
  return request<ReopenResult>(`/api/v1/decisions/${id}/reopen`, {
    method: "POST",
    body: JSON.stringify({ new_information: newInformation }),
  });
}
