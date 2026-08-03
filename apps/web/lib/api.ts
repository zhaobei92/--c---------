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

export interface DecisionDetail extends DecisionSummary {
  facts: string[];
  concerns: string[];
  unknowns: string[];
  options: DecisionOption[];
  constraints: HardConstraint[];
  messages: DecisionMessage[];
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
