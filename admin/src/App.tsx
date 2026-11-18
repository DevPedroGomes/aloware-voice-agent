import { useCallback, useEffect, useMemo, useState } from "react";
import { api, CallDetail, CallSummary, Catalog, Config, EvalRun, Lead } from "./api";

type Tab = "agent" | "calls" | "evals" | "compliance";

export default function App() {
  const [tab, setTab] = useState<Tab>("agent");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [saved, setSaved] = useState<Config | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);

  const reload = useCallback(async () => {
    const [cat, cfg, ls] = await Promise.all([api.catalog(), api.config(), api.leads()]);
    setCatalog(cat);
    setSaved(cfg);
    setDraft(structuredClone(cfg));
    setLeads(ls);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (!catalog || !saved || !draft) return <div className="loading">Loading… (is the API running on :8000?)</div>;

  return (
    <div className="app">
      <header>
        <div>
          <h1>Speed-to-lead voice agent</h1>
          <span className="muted">
            {saved.persona.name} · {saved.persona.company}
          </span>
        </div>
        <span className="badge" title={`Saved ${saved.updated_at}`}>
          live config v{saved.version}
        </span>
      </header>
      <nav>
        {(["agent", "calls", "evals", "compliance"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "agent" ? "Agent config" : t === "calls" ? "Calls" : t === "evals" ? "Evals" : "Leads & DNC"}
          </button>
        ))}
      </nav>
      {tab === "agent" && (
        <AgentTab
          catalog={catalog}
          saved={saved}
          draft={draft}
          setDraft={setDraft}
          leads={leads}
          onSaved={async (cfg) => {
            setSaved(cfg);
            setDraft(structuredClone(cfg));
            setLeads(await api.leads());
          }}
        />
      )}
      {tab === "calls" && <CallsTab />}
      {tab === "evals" && <EvalsTab currentVersion={saved.version} />}
      {tab === "compliance" && <ComplianceTab leads={leads} refreshLeads={async () => setLeads(await api.leads())} />}
    </div>
  );
}

// ---------------------------------------------------------------- agent config

function AgentTab(props: {
  catalog: Catalog;
  saved: Config;
  draft: Config;
  setDraft: (c: Config) => void;
  leads: Lead[];
  onSaved: (c: Config) => void;
}) {
  const { catalog, saved, draft, setDraft, leads } = props;
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<string>("");
  const [preview, setPreview] = useState<{ text: string; has_ai_disclosure: boolean } | null>(null);
  const [versions, setVersions] = useState<{ version: number; updated_at: string }[]>([]);
  const [showRules, setShowRules] = useState(false);
  const dirty = useMemo(() => JSON.stringify(saved) !== JSON.stringify(draft), [saved, draft]);

  const update = (fn: (c: Config) => void) => {
    const next = structuredClone(draft);
    fn(next);
    setDraft(next);
    setNotice("");
  };

  useEffect(() => {
    const t = setTimeout(() => {
      api.preview(draft.greeting, draft.persona, draft.active_lead_id).then(setPreview).catch(() => setPreview(null));
    }, 250);
    return () => clearTimeout(t);
  }, [draft.greeting, draft.persona, draft.active_lead_id]);

  useEffect(() => {
    api.versions().then(setVersions);
  }, [saved.version]);

  const save = async () => {
    try {
      const cfg = await api.saveConfig(draft);
      setErrors([]);
      setNotice(`Saved as v${cfg.version}. The next call uses it, no restart needed.`);
      props.onSaved(cfg);
    } catch (e: any) {
      setErrors(e?.detail?.errors ?? [JSON.stringify(e)]);
    }
  };

  const rollback = async (v: number) => {
    const cfg = await api.rollback(v);
    setNotice(`Rolled back: v${v} re-published as v${cfg.version}.`);
    props.onSaved(cfg);
  };

  const activeLead = leads.find((l) => l.id === draft.active_lead_id);

  return (
    <div className="grid">
      <div className="col">
        <section>
          <h2>Persona</h2>
          <label>
            Name
            <input value={draft.persona.name} onChange={(e) => update((c) => (c.persona.name = e.target.value))} />
          </label>
          <label>
            Company
            <input value={draft.persona.company} onChange={(e) => update((c) => (c.persona.company = e.target.value))} />
          </label>
          <label>
            Voice
            <select value={draft.persona.voice} onChange={(e) => update((c) => (c.persona.voice = e.target.value))}>
              {Object.entries(catalog.voices).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </section>

        <section>
          <h2>Greeting</h2>
          <p className="hint">
            Spoken word for word when the lead picks up. Variables:{" "}
            {Object.keys(catalog.template_vars).map((v) => (
              <code key={v} title={catalog.template_vars[v]}>{`{{${v}}}`}</code>
            ))}{" "}
            Add a fallback with <code>{"{{first_name|there}}"}</code>.
          </p>
          <textarea rows={4} value={draft.greeting} onChange={(e) => update((c) => (c.greeting = e.target.value))} />
          {preview && (
            <div className={`preview ${preview.has_ai_disclosure ? "" : "warn"}`}>
              <strong>{activeLead ? `To ${activeLead.first_name || "(no name)"}:` : "Preview:"}</strong> “{preview.text}”
              {!preview.has_ai_disclosure && <div>⚠ Must say it's an AI (FCC 2024). Saving will be blocked.</div>}
            </div>
          )}
        </section>

        <section>
          <h2>Instructions</h2>
          <p className="hint">The campaign script. Phone etiquette and compliance rules are added on top and can't be edited here.</p>
          <textarea rows={14} value={draft.instructions} onChange={(e) => update((c) => (c.instructions = e.target.value))} />
          <button className="link" onClick={() => setShowRules(!showRules)}>
            {showRules ? "Hide" : "Show"} the always-on system rules
          </button>
          {showRules && <pre className="rules">{catalog.system_rules}</pre>}
        </section>
      </div>

      <div className="col">
        <section>
          <h2>Tools</h2>
          {Object.entries(catalog.tools).map(([name, meta]) => (
            <label key={name} className="check">
              <input
                type="checkbox"
                checked={!!draft.tools[name]}
                disabled={meta.locked}
                onChange={(e) => update((c) => (c.tools[name] = e.target.checked))}
              />
              <span>
                <strong>{meta.label}</strong> {meta.locked && <span className="lock">always on</span>}
                <br />
                <span className="muted">{meta.description}</span>
              </span>
            </label>
          ))}
        </section>

        <section>
          <h2>Who to call</h2>
          <label>
            Lead for the next call
            <select value={draft.active_lead_id} onChange={(e) => update((c) => (c.active_lead_id = e.target.value))}>
              {leads.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.first_name || "(no name)"} {l.last_name} · {l.city}, {l.state} {l.gate.allowed ? "" : "· BLOCKED"}
                </option>
              ))}
            </select>
          </label>
          {activeLead && !activeLead.gate.allowed && <div className="preview warn">Blocked before dialing: {activeLead.gate.reason}</div>}
          <label className="check">
            <input
              type="checkbox"
              checked={draft.compliance.enforce_calling_hours}
              onChange={(e) => update((c) => (c.compliance.enforce_calling_hours = e.target.checked))}
            />
            <span>
              Enforce calling hours in the lead's local time
              <br />
              <span className="muted">Turn off only to demo outside US hours.</span>
            </span>
          </label>
          <div className="row">
            <label>
              From
              <input
                type="number"
                min={8}
                max={21}
                value={draft.compliance.calling_hours_start}
                onChange={(e) => update((c) => (c.compliance.calling_hours_start = Number(e.target.value)))}
              />
            </label>
            <label>
              To
              <input
                type="number"
                min={8}
                max={21}
                value={draft.compliance.calling_hours_end}
                onChange={(e) => update((c) => (c.compliance.calling_hours_end = Number(e.target.value)))}
              />
            </label>
          </div>
        </section>

        <section>
          <h2>Model</h2>
          <label>
            LLM (LiveKit Inference)
            <select value={draft.models.llm} onChange={(e) => update((c) => (c.models.llm = e.target.value))}>
              {["openai/gpt-5.4-mini", "google/gemini-3.5-flash", "google/gemma-4-31b-it", "openai/gpt-5.4"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
        </section>

        <section className="savebar">
          {errors.length > 0 && (
            <ul className="errors">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          {notice && <div className="ok">{notice}</div>}
          <div className="row">
            <button className="primary" disabled={!dirty} onClick={save}>
              {dirty ? `Save as v${saved.version + 1}` : "No changes"}
            </button>
            {dirty && <button onClick={() => props.setDraft(structuredClone(saved))}>Discard</button>}
          </div>
        </section>

        <section>
          <h2>Versions</h2>
          <table>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version}>
                  <td>v{v.version}</td>
                  <td className="muted">{fmt(v.updated_at)}</td>
                  <td>
                    {v.version === saved.version ? (
                      <span className="badge small">live</span>
                    ) : (
                      <button className="link" onClick={() => rollback(v.version)}>
                        roll back
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- calls

function CallsTab() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [open, setOpen] = useState<CallDetail | null>(null);

  useEffect(() => {
    const load = () => api.calls().then(setCalls);
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="grid">
      <div className="col">
        <section>
          <h2>Recent calls</h2>
          {calls.length === 0 && <p className="muted">No calls yet. Run the agent and talk to it.</p>}
          <table className="hover">
            <thead>
              <tr>
                <th>When</th>
                <th>Lead</th>
                <th>Config</th>
                <th>Outcome</th>
                <th>p50 reply</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.call_id} onClick={() => api.call(c.call_id).then(setOpen)} className={open?.call_id === c.call_id ? "sel" : ""}>
                  <td>{fmt(c.started_at)}</td>
                  <td>{c.lead_name || "(no name)"}</td>
                  <td>v{c.config_version}</td>
                  <td>
                    <span className={`disp ${c.disposition}`}>{c.disposition}</span>
                  </td>
                  <td>{sec(c.latency?.e2e_p50)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
      <div className="col">
        {open && (
          <section>
            <h2>
              Call {open.call_id} <span className="muted">· {open.persona} · config v{open.config_version}</span>
            </h2>
            {open.blocked && <div className="preview warn">Blocked before dialing: {open.blocked.reason}</div>}
            {open.crm_note && (
              <p>
                <strong>CRM note:</strong> {open.crm_note}
              </p>
            )}
            {open.appointment && (
              <p>
                <strong>Booked:</strong> {open.appointment.spoken}. Notes for the agent: {open.appointment.notes}
              </p>
            )}
            {open.transfer && (
              <p>
                <strong>Transferred</strong> ({open.transfer.reason}). Whisper: {open.transfer.whisper}
              </p>
            )}
            <h3>Transcript</h3>
            <div className="transcript">
              {open.transcript.map((m, i) => (
                <div key={i} className={`msg ${m.role}`}>
                  <span>{m.role === "assistant" ? open.persona : "Lead"}</span>
                  {m.text}
                  {m.interrupted && <em> (interrupted)</em>}
                </div>
              ))}
            </div>
            <h3>Tool calls</h3>
            {open.tool_calls.length === 0 && <p className="muted">None.</p>}
            {open.tool_calls.map((t, i) => (
              <div key={i} className={`tool ${t.is_error ? "err" : ""}`}>
                <code>
                  {t.name}({t.arguments})
                </code>
                {t.output && <div className="muted">→ {t.output}</div>}
              </div>
            ))}
            <h3>Latency per reply</h3>
            <table>
              <thead>
                <tr>
                  <th>Reply</th>
                  <th>end-to-end</th>
                  <th>LLM first token</th>
                  <th>TTS first byte</th>
                </tr>
              </thead>
              <tbody>
                {open.latency.turns.map((t, i) => (
                  <tr key={i}>
                    <td className="muted">{t.text}</td>
                    <td>{sec(t.e2e_latency)}</td>
                    <td>{sec(t.llm_ttft)}</td>
                    <td>{sec(t.tts_ttfb)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- evals

function EvalsTab({ currentVersion }: { currentVersion: number }) {
  const [state, setState] = useState<{ running: boolean; latest: EvalRun | null }>({ running: false, latest: null });

  useEffect(() => {
    const load = () => api.evals().then(setState);
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const latest = state.latest;
  const stale = latest && latest.config_version !== currentVersion;

  return (
    <section>
      <h2>Evals</h2>
      <p className="hint">
        Runs the test suite against the config that is live right now (v{currentVersion}): deterministic compliance checks plus short
        text-mode calls against the real model. Run it after every prompt or tool change.
      </p>
      <button className="primary" disabled={state.running} onClick={() => api.runEvals().then(() => setState({ ...state, running: true }))}>
        {state.running ? "Running… (about a minute)" : `Run evals on v${currentVersion}`}
      </button>
      {stale && <div className="preview warn">The last run was on v{latest!.config_version}. The config changed since then.</div>}
      {latest && (
        <>
          <p>
            <strong className="pass">{latest.passed} passed</strong> · <strong className="fail">{latest.failed} failed</strong> ·{" "}
            {latest.skipped} skipped · config v{latest.config_version} · {fmt(latest.finished_at)}
          </p>
          <table>
            <tbody>
              {latest.cases.map((c) => (
                <tr key={c.file + c.name}>
                  <td>
                    <span className={c.status === "passed" ? "pass" : c.status === "failed" ? "fail" : "muted"}>{c.status}</span>
                  </td>
                  <td>
                    <code>{c.name}</code>
                    {c.message && <div className="fail small">{c.message.slice(0, 300)}</div>}
                  </td>
                  <td className="muted">{c.file}</td>
                  <td className="muted">{c.seconds.toFixed(1)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- leads & DNC

function ComplianceTab({ leads, refreshLeads }: { leads: Lead[]; refreshLeads: () => void }) {
  const [dnc, setDnc] = useState<Awaited<ReturnType<typeof api.dnc>>>([]);
  const [appts, setAppts] = useState<Awaited<ReturnType<typeof api.appointments>>>([]);
  const load = () => {
    api.dnc().then(setDnc);
    api.appointments().then(setAppts);
    refreshLeads();
  };
  useEffect(load, []);

  const remove = async (phone: string) => {
    const reason = window.prompt("Why is this number being removed from Do Not Call? (audited, 10+ characters)");
    if (!reason) return;
    try {
      await api.dncRemove(phone, reason);
      load();
    } catch (e: any) {
      alert(e?.detail ?? "Failed");
    }
  };

  return (
    <div className="grid">
      <div className="col">
        <section>
          <h2>Leads and the pre-call gate</h2>
          <table>
            <thead>
              <tr>
                <th>Lead</th>
                <th>Consent</th>
                <th>Can we call now?</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr key={l.id}>
                  <td>
                    {l.first_name || "(no name)"} {l.last_name}
                    <div className="muted small">
                      {l.phone} · {l.timezone}
                    </div>
                  </td>
                  <td className="small">{l.consent.given ? l.consent.method : "none on file"}</td>
                  <td className="small">{l.gate.allowed ? <span className="pass">yes</span> : <span className="fail">{l.gate.reason}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section>
          <h2>Booked appointments</h2>
          {appts.length === 0 && <p className="muted">None yet.</p>}
          {appts.map((a, i) => (
            <p key={i}>
              <strong>{a.spoken}</strong> · {a.lead_id}
              <br />
              <span className="muted small">{a.notes}</span>
            </p>
          ))}
        </section>
      </div>
      <div className="col">
        <section>
          <h2>Do Not Call list</h2>
          {dnc.length === 0 && <p className="muted">Empty.</p>}
          <table>
            <tbody>
              {dnc.map((d) => (
                <tr key={d.phone}>
                  <td>{d.phone}</td>
                  <td className="small">
                    “{d.reason}”<div className="muted">via {d.source}</div>
                  </td>
                  <td>
                    <button className="link" onClick={() => remove(d.phone)}>
                      remove…
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}

function fmt(iso?: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function sec(v?: number | null) {
  return v == null ? "–" : `${v.toFixed(2)} s`;
}
