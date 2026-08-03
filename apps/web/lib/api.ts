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

export interface DecisionDetail extends DecisionSummary {
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
