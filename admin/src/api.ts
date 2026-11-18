export type Config = {
  version: number;
  updated_at: string;
  persona: { name: string; company: string; voice: string };
  greeting: string;
  instructions: string;
  tools: Record<string, boolean>;
  compliance: { enforce_calling_hours: boolean; calling_hours_start: number; calling_hours_end: number };
  models: { stt: string; llm: string };
  active_lead_id: string;
};

export type Catalog = {
  voices: Record<string, string>;
  tools: Record<string, { label: string; description: string; locked: boolean }>;
  template_vars: Record<string, string>;
  system_rules: string;
};

export type Lead = {
  id: string;
  first_name: string;
  last_name: string;
  phone: string;
  city: string;
  state: string;
  timezone: string;
  source: string;
  consent: { given: boolean; method: string };
  gate: { allowed: boolean; code: string | null; reason: string | null };
};

export type Turn = { text: string; e2e_latency: number | null; llm_ttft: number | null; tts_ttfb: number | null };

export type CallSummary = {
  call_id: string;
  started_at: string;
  config_version: number;
  lead_name: string;
  disposition: string;
  latency: { turns: Turn[]; e2e_p50: number | null; e2e_max: number | null };
};

export type CallDetail = CallSummary & {
  persona: string;
  crm_note: string | null;
  blocked: { code: string; reason: string } | null;
  transcript: { role: string; text: string; interrupted: boolean }[];
  tool_calls: { name: string; arguments: string; output?: string; is_error?: boolean }[];
  appointment: { spoken: string; notes: string } | null;
  transfer: { reason: string; whisper: string } | null;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw body;
  return body as T;
}

export const api = {
  catalog: () => req<Catalog>("/api/catalog"),
  config: () => req<Config>("/api/config"),
  saveConfig: (cfg: Config) => req<Config>("/api/config", { method: "PUT", body: JSON.stringify(cfg) }),
  preview: (greeting: string, persona: Config["persona"], lead_id: string) =>
    req<{ text: string; has_ai_disclosure: boolean }>("/api/preview-greeting", {
      method: "POST",
      body: JSON.stringify({ greeting, persona, lead_id }),
    }),
  leads: () => req<Lead[]>("/api/leads"),
  calls: () => req<CallSummary[]>("/api/calls"),
  call: (id: string) => req<CallDetail>(`/api/calls/${id}`),
  dnc: () => req<{ phone: string; reason: string; source: string; added_at: string }[]>("/api/dnc"),
};
