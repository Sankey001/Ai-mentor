import { useCallback, useEffect, useState } from "react";
import { Save } from "lucide-react";
import { getSettings, saveSettings } from "../service/mentorLive";
import "../components/learning/learning-workspace.css";
import "../components/mentor/mentor.css";

// Text settings shown as editable fields, by section. Keys are .env names.
const REPLY_FIELDS = [
  ["OLLAMA_URL", "Ollama address", "Where the local Ollama server listens."],
  ["GEMINI_MODEL", "Gemini model", "Used when replies run on the Gemini API."],
];
const PATH_FIELDS = [
  ["MUSETALK_DIR", "Lip-sync model folder (MuseTalk)", "The MuseTalk folder with its models."],
  ["FACE_WORKER_PYTHON", "Lip-sync Python", "python.exe of the lip-sync environment."],
  ["VOICE_WORKER_PYTHON", "Voice-cloning Python", "python.exe of the voice environment."],
];
const KEYS = [
  ["GEMINI_API_KEY", "Gemini API key", "https://aistudio.google.com/apikey"],
  ["GROQ_API_KEY", "Groq API key", "https://console.groq.com/keys"],
];

const textValues = (settings) => {
  const values = {};
  for (const [name, entry] of Object.entries(settings.values)) {
    if ("value" in entry) values[name] = entry.value;
  }
  values.OLLAMA_MODELS = settings.ollama_models.value;
  return values;
};

function PathState({ exists }) {
  return (
    <span className="mt-badge" data-state={exists ? "ready" : "failed"}>
      {exists ? "Found" : "Not found"}
    </span>
  );
}

export default function MentorSettings() {
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState({});
  const [keys, setKeys] = useState({});
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);

  const show = useCallback((loaded) => {
    setSettings(loaded);
    setForm(textValues(loaded));
    setKeys({});
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getSettings(controller.signal)
      .then(show)
      .catch((error) => {
        if (error.name !== "AbortError") setLoadError(error.message);
      });
    return () => controller.abort();
  }, [show]);

  if (!settings) {
    return (
      <div className="lw-page">
        <div className="lw-inner">
          <header className="lw-heading">
            <h1>AI settings</h1>
          </header>
          <p className="mt-notice" role={loadError ? "alert" : "status"}>
            {loadError ? `Settings could not be loaded. ${loadError}` : "Loading settings…"}
          </p>
        </div>
      </div>
    );
  }

  const saved = textValues(settings);
  const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  // Only what changed is sent; a key is sent when typed, or as "" to remove it.
  const changes = {
    ...Object.fromEntries(Object.entries(form).filter(([name, value]) => value !== saved[name])),
    ...keys,
  };
  const changed = Object.keys(changes).length > 0;
  const installed = settings.ollama_models.installed;

  const submit = async (event) => {
    event.preventDefault();
    if (!changed) return;
    setSaving(true);
    setResult(null);
    try {
      const next = await saveSettings(changes);
      show(next);
      setResult({
        ok: true,
        text:
          "Saved. Restart the app (start_app.py) to use the new settings." +
          ("OLLAMA_MODELS" in changes
            ? " Restart Ollama too, so it reads the new models folder."
            : ""),
      });
    } catch (error) {
      setResult({ ok: false, text: error.message });
    } finally {
      setSaving(false);
    }
  };

  const textField = ([name, label, help]) => (
    <div className="mt-field" key={name}>
      <label htmlFor={`setting-${name}`}>{label}</label>
      <input
        id={`setting-${name}`}
        className="mt-input"
        value={form[name] ?? ""}
        onChange={(event) => set(name, event.target.value)}
        spellCheck={false}
      />
      <span className="mt-help">{help}</span>
    </div>
  );

  const pathField = ([name, label, help]) => (
    <div className="mt-field" key={name}>
      <label htmlFor={`setting-${name}`}>
        {label} <PathState exists={settings.values[name].exists} />
      </label>
      <input
        id={`setting-${name}`}
        className="mt-input"
        value={form[name] ?? ""}
        onChange={(event) => set(name, event.target.value)}
        spellCheck={false}
      />
      <span className="mt-help">{help}</span>
    </div>
  );

  return (
    <div className="lw-page">
      <div className="lw-inner">
        <header className="lw-heading">
          <span className="lw-eyebrow">LIVE MENTOR</span>
          <h1>AI settings</h1>
        </header>
        <p className="mt-notice">
          Saved to <code>{settings.env_file}</code>. Changes apply after the app restarts. These
          settings can only be changed on this computer.
        </p>

        <form className="mt-settings" onSubmit={submit} noValidate>
          <section className="mt-card" aria-labelledby="settings-replies">
            <h2 id="settings-replies" className="mt-settings-title">
              Replies
            </h2>
            <div className="mt-field">
              <label htmlFor="setting-AI_PROVIDER">Default AI</label>
              <select
                id="setting-AI_PROVIDER"
                className="mt-select"
                style={{ textTransform: "none" }}
                value={form.AI_PROVIDER || "gemini"}
                onChange={(event) => set("AI_PROVIDER", event.target.value)}
              >
                <option value="ollama">Local model on this computer (Ollama)</option>
                <option value="gemini">Gemini API</option>
              </select>
              <span className="mt-help">
                Used for quizzes, flashcards and syllabuses, and for the chat until a learner picks
                another model in the classroom.
              </span>
            </div>
            <div className="mt-field">
              <label htmlFor="setting-OLLAMA_MODEL">Default local model</label>
              <select
                id="setting-OLLAMA_MODEL"
                className="mt-select"
                style={{ textTransform: "none" }}
                value={form.OLLAMA_MODEL || ""}
                onChange={(event) => set("OLLAMA_MODEL", event.target.value)}
              >
                {!installed.includes(form.OLLAMA_MODEL) && (
                  <option value={form.OLLAMA_MODEL || ""}>
                    {form.OLLAMA_MODEL ? `${form.OLLAMA_MODEL} (not installed)` : "None"}
                  </option>
                )}
                {installed.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            {REPLY_FIELDS.map(textField)}
          </section>

          <section className="mt-card" aria-labelledby="settings-keys">
            <h2 id="settings-keys" className="mt-settings-title">
              API keys
            </h2>
            {KEYS.map(([name, label, link]) => {
              const key = settings.values[name];
              const removing = keys[name] === "";
              return (
                <div className="mt-field" key={name}>
                  <label htmlFor={`setting-${name}`}>
                    {label}{" "}
                    <span className="mt-badge" data-state={key.set && !removing ? "ready" : ""}>
                      {removing ? "Will be removed" : key.set ? `Saved …${key.hint}` : "Not set"}
                    </span>
                  </label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      id={`setting-${name}`}
                      className="mt-input"
                      type="password"
                      autoComplete="off"
                      placeholder={key.set ? "Type a new key to replace it" : "Paste your key"}
                      value={removing ? "" : keys[name] || ""}
                      onChange={(event) =>
                        setKeys((current) => {
                          const next = { ...current, [name]: event.target.value };
                          if (!event.target.value) delete next[name];
                          return next;
                        })
                      }
                    />
                    {key.set && (
                      <button
                        type="button"
                        className="lw-button"
                        onClick={() =>
                          setKeys((current) => {
                            const next = { ...current };
                            if (removing) delete next[name];
                            else next[name] = "";
                            return next;
                          })
                        }
                      >
                        {removing ? "Keep" : "Remove"}
                      </button>
                    )}
                  </div>
                  <span className="mt-help">
                    Get one at{" "}
                    <a href={link} target="_blank" rel="noreferrer">
                      {link.replace("https://", "")}
                    </a>
                    . Saved keys are never shown again.
                  </span>
                </div>
              );
            })}
          </section>

          <section className="mt-card" aria-labelledby="settings-paths">
            <h2 id="settings-paths" className="mt-settings-title">
              Model locations
            </h2>
            <div className="mt-field">
              <label htmlFor="setting-OLLAMA_MODELS">
                Local AI models folder (Ollama) <PathState exists={settings.ollama_models.exists} />
              </label>
              <input
                id="setting-OLLAMA_MODELS"
                className="mt-input"
                value={form.OLLAMA_MODELS ?? ""}
                onChange={(event) => set("OLLAMA_MODELS", event.target.value)}
                disabled={!settings.ollama_models.editable}
                spellCheck={false}
              />
              <span className="mt-help">
                {settings.ollama_models.source} (OLLAMA_MODELS).{" "}
                {installed.length
                  ? `Ollama is serving ${installed.length} model${installed.length === 1 ? "" : "s"}: ${installed.join(", ")}.`
                  : "Ollama is not running, so no models are listed."}{" "}
                The folder holds Ollama&apos;s <code>blobs</code> and <code>manifests</code>.
              </span>
            </div>
            <div className="mt-field">
              <span className="mt-label">
                Voice models cache (Hugging Face) <PathState exists={settings.huggingface.exists} />
              </span>
              <code className="mt-help">
                {settings.huggingface.value || "Default (~/.cache/huggingface)"}
              </code>
              <span className="mt-help">Set with the HF_HOME environment variable.</span>
            </div>
            {PATH_FIELDS.map(pathField)}
          </section>

          <div className="mt-settings-actions">
            {result && (
              <p
                className={result.ok ? "mt-help" : "mt-error"}
                role={result.ok ? "status" : "alert"}
              >
                {result.text}
              </p>
            )}
            <button
              type="submit"
              className="lw-button lw-button--primary"
              disabled={!changed || saving}
            >
              <Save size={16} aria-hidden="true" />
              <span>{saving ? "Saving…" : "Save settings"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
