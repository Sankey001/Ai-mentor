import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ImagePlus, Mic, Pencil, Square, Trash2, Undo2, Upload, X } from "lucide-react";
import {
  MAX_IMAGES,
  MAX_PERSONA_CHARS,
  createMentor,
  deleteMentor,
  formatPitch,
  formatRate,
  getMentor,
  listMentors,
  listVoices,
  mentorImageUrl,
  parseAdjustment,
  updateMentor,
  validateMentorForm,
} from "../service/mentorLive";
import "../components/learning/learning-workspace.css";
import "../components/mentor/mentor.css";

const STATE_LABEL = { ready: "Ready", preparing: "Preparing", failed: "Failed" };
const emptyForm = {
  name: "",
  persona: "",
  expressiveness: 0.5,
  images: [],
  voice: null,
  consent: false,
  voiceId: "",
  rate: 0,
  pitch: 0,
};
// What an edited mentor already has on the server.
const emptySaved = { images: [], ownVoice: false };

function Thumb({ src, label, removed, onToggle, toggleLabel }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className="mt-thumb" data-removed={removed || undefined}>
      {/* Formats the browser cannot preview (such as HEIC) still work; show their name. */}
      {src && !broken ? <img src={src} alt="" onError={() => setBroken(true)} /> : label}
      <button type="button" onClick={onToggle} aria-label={toggleLabel}>
        {removed ? <Undo2 size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
      </button>
    </div>
  );
}

function NewImageThumb({ file, onRemove }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return (
    <Thumb src={url} label={file.name} onToggle={onRemove} toggleLabel={`Remove ${file.name}`} />
  );
}

export default function MentorStudio() {
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(""); // name of the mentor being edited, or ""
  const [saved, setSaved] = useState(emptySaved);
  const [removeImages, setRemoveImages] = useState([]);
  const [removeVoice, setRemoveVoice] = useState(false);
  const [errors, setErrors] = useState({});
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [mentors, setMentors] = useState([]);
  const [voices, setVoices] = useState([]);
  const [serviceDown, setServiceDown] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [recording, setRecording] = useState(false);
  const [voiceUrl, setVoiceUrl] = useState("");
  const recorderRef = useRef(null);
  const fileInputRef = useRef(null);
  const formRef = useRef(null);

  const set = (patch) => setForm((current) => ({ ...current, ...patch }));
  const keptImages = saved.images.filter((filename) => !removeImages.includes(filename));
  const photoCount = keptImages.length + form.images.length;

  const refresh = useCallback(async () => {
    try {
      setMentors(await listMentors());
      setServiceDown(false);
    } catch {
      setServiceDown(true);
    }
  }, []);

  // Poll faster while a mentor is being prepared.
  const preparing = mentors.some((mentor) => mentor.state === "preparing");
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, preparing ? 4000 : 15000);
    return () => clearInterval(timer);
  }, [refresh, preparing]);

  useEffect(() => {
    listVoices()
      .then(setVoices)
      .catch(() => setVoices([]));
  }, []);

  useEffect(() => {
    if (!form.voice) {
      setVoiceUrl("");
      return undefined;
    }
    const objectUrl = URL.createObjectURL(form.voice);
    setVoiceUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [form.voice]);

  useEffect(
    () => () => recorderRef.current?.stream?.getTracks().forEach((track) => track.stop()),
    [],
  );

  const resetForm = () => {
    setForm(emptyForm);
    setEditing("");
    setSaved(emptySaved);
    setRemoveImages([]);
    setRemoveVoice(false);
    setErrors({});
    setSubmitError("");
  };

  const startEditing = async (name) => {
    setSubmitError("");
    setNotice("");
    try {
      const details = await getMentor(name);
      setForm({
        ...emptyForm,
        name,
        persona: details.persona,
        expressiveness: details.expressiveness,
        voiceId: details.voice?.voice_id || "",
        rate: parseAdjustment(details.voice?.rate),
        pitch: parseAdjustment(details.voice?.pitch),
      });
      setSaved({ images: details.images, ownVoice: details.own_voice });
      setRemoveImages([]);
      setRemoveVoice(false);
      setErrors({});
      setEditing(name);
      formRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    } catch (error) {
      setSubmitError(error.message);
    }
  };

  const addImages = (files) => {
    const room = MAX_IMAGES - keptImages.length;
    set({ images: [...form.images, ...Array.from(files || [])].slice(0, Math.max(0, room)) });
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const parts = [];
      recorder.ondataavailable = (event) => parts.push(event.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const type = recorder.mimeType || "audio/webm";
        set({ voice: new File(parts, "recording.webm", { type }) });
        setRecording(false);
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setErrors((current) => ({
        ...current,
        voice: "The microphone is not available. Upload a recording instead.",
      }));
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    const name = editing || form.name.trim().toLowerCase();
    const problems = validateMentorForm(
      { ...form, name },
      editing ? { existingImages: keptImages.length } : undefined,
    );
    setErrors(problems);
    setSubmitError("");
    setNotice("");
    if (Object.keys(problems).length) return;
    setSubmitting(true);
    try {
      if (editing) {
        const result = await updateMentor(name, { ...form, removeImages, removeVoice });
        setNotice(
          result.state === "preparing"
            ? `“${name}” was saved. Its photos changed, so it is being prepared again.`
            : `“${name}” was saved.`,
        );
      } else {
        await createMentor({ ...form, name });
        setNotice(
          `“${name}” was saved and is being prepared. This takes a few minutes; you can leave this page.`,
        );
      }
      resetForm();
      await refresh();
    } catch (error) {
      setSubmitError(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (name) => {
    if (!window.confirm(`Remove the mentor “${name}” and its files?`)) return;
    try {
      await deleteMentor(name);
      if (editing === name) resetForm();
    } catch (error) {
      setSubmitError(error.message);
    }
    refresh();
  };

  const toggleSavedImage = (filename) =>
    setRemoveImages((current) =>
      current.includes(filename)
        ? current.filter((item) => item !== filename)
        : [...current, filename],
    );

  const delivery =
    form.expressiveness < 0.45 ? "calm" : form.expressiveness > 0.55 ? "lively" : "balanced";
  const hasOwnVoice = Boolean(form.voice) || (saved.ownVoice && !removeVoice);

  return (
    <div className="lw-page">
      <div className="lw-inner">
        <nav className="lw-breadcrumb" aria-label="Breadcrumb">
          <Link to="/mentor" className="inline-flex items-center gap-1">
            <ArrowLeft aria-hidden="true" /> Back to the classroom
          </Link>
        </nav>
        <header className="lw-heading">
          <span className="lw-eyebrow">MENTOR STUDIO</span>
          <h1>{editing ? `Edit “${editing}”` : "Create a mentor from photos and a voice"}</h1>
        </header>

        {serviceDown && (
          <p className="mt-notice" role="alert">
            The mentor service is not reachable. Start it with <code>start_app.py</code> to create
            mentors.
          </p>
        )}

        <div className="mt-studio">
          <form className="mt-card" onSubmit={submit} noValidate ref={formRef}>
            {!editing && (
              <div className="mt-field">
                <label htmlFor="mentor-name">Mentor name</label>
                <input
                  id="mentor-name"
                  className="mt-input"
                  value={form.name}
                  maxLength={50}
                  onChange={(event) => set({ name: event.target.value })}
                  placeholder="for example: asha"
                  aria-describedby="mentor-name-help"
                  aria-invalid={Boolean(errors.name)}
                />
                <span className="mt-help" id="mentor-name-help">
                  Lowercase letters, digits, hyphens or underscores.
                </span>
                {errors.name && <span className="mt-error">{errors.name}</span>}
              </div>
            )}

            <div className="mt-field">
              <span className="mt-label" id="images-label">
                Photos of the mentor ({photoCount}/{MAX_IMAGES})
              </span>
              {(saved.images.length > 0 || form.images.length > 0) && (
                <div className="mt-thumbs">
                  {saved.images.map((filename) => {
                    const removed = removeImages.includes(filename);
                    return (
                      <Thumb
                        key={filename}
                        src={mentorImageUrl(editing, filename)}
                        label={filename}
                        removed={removed}
                        onToggle={() => toggleSavedImage(filename)}
                        toggleLabel={removed ? `Keep ${filename}` : `Remove ${filename}`}
                      />
                    );
                  })}
                  {form.images.map((file, index) => (
                    <NewImageThumb
                      key={`${file.name}-${index}`}
                      file={file}
                      onRemove={() => set({ images: form.images.filter((_, i) => i !== index) })}
                    />
                  ))}
                </div>
              )}
              <div
                className="mt-drop"
                data-active={dragging}
                role="button"
                tabIndex={0}
                aria-labelledby="images-label"
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") fileInputRef.current?.click();
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  addImages(event.dataTransfer.files);
                }}
              >
                <ImagePlus size={22} aria-hidden="true" />
                <span>
                  {editing
                    ? "Drop more images here or click to choose"
                    : "Drop images here or click to choose"}
                </span>
                <span className="mt-help">
                  Any image format. Clear, front-facing photos with the mouth closed work best.
                </span>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                data-testid="image-input"
                onChange={(event) => {
                  addImages(event.target.files);
                  event.target.value = "";
                }}
              />
              {editing && (removeImages.length > 0 || form.images.length > 0) && (
                <span className="mt-help">
                  Changing the photos prepares the mentor again, which takes a few minutes.
                </span>
              )}
              {errors.images && <span className="mt-error">{errors.images}</span>}
            </div>

            <div className="mt-field">
              <span className="mt-label">Own voice (optional)</span>
              {saved.ownVoice && !form.voice && (
                <span className="mt-help" role="status">
                  {removeVoice
                    ? "The saved recording will be removed when you save."
                    : "This mentor has a saved voice recording."}
                </span>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <label className="lw-button" style={{ cursor: "pointer" }}>
                  <Upload size={16} aria-hidden="true" />
                  <span>{saved.ownVoice ? "Replace the recording" : "Upload a recording"}</span>
                  <input
                    type="file"
                    accept="audio/*,video/*"
                    hidden
                    data-testid="voice-input"
                    onChange={(event) => {
                      set({ voice: event.target.files?.[0] || null });
                      event.target.value = "";
                    }}
                  />
                </label>
                {typeof MediaRecorder !== "undefined" &&
                  (recording ? (
                    <button
                      type="button"
                      className="lw-button lw-button--selected"
                      onClick={() => recorderRef.current?.stop()}
                    >
                      <Square size={15} aria-hidden="true" />
                      <span>Stop recording</span>
                    </button>
                  ) : (
                    <button type="button" className="lw-button" onClick={startRecording}>
                      <Mic size={16} aria-hidden="true" />
                      <span>Record now</span>
                    </button>
                  ))}
                {form.voice && (
                  <button
                    type="button"
                    className="lw-button lw-button--quiet"
                    onClick={() => set({ voice: null })}
                  >
                    <X size={15} aria-hidden="true" />
                    <span>Discard the new recording</span>
                  </button>
                )}
                {saved.ownVoice && !form.voice && (
                  <button
                    type="button"
                    className="lw-button lw-button--quiet"
                    onClick={() => setRemoveVoice((value) => !value)}
                  >
                    {removeVoice ? "Keep the saved recording" : "Remove the saved recording"}
                  </button>
                )}
              </div>
              {voiceUrl && <audio controls src={voiceUrl} className="w-full mt-1" />}
              <span className="mt-help">
                10 to 30 seconds of clear speech, no music. The mentor speaks in this voice and
                accent.
              </span>
              {errors.voice && <span className="mt-error">{errors.voice}</span>}
            </div>

            <div className="mt-field">
              <label htmlFor="mentor-voice">Standard voice</label>
              <select
                id="mentor-voice"
                className="mt-select"
                style={{ textTransform: "none" }}
                value={form.voiceId}
                onChange={(event) => set({ voiceId: event.target.value })}
              >
                <option value="">Default voice</option>
                {voices.map((voice) => (
                  <option key={voice.voice_id} value={voice.voice_id}>
                    {voice.name} · {voice.gender} · {voice.language} ({voice.accent})
                  </option>
                ))}
              </select>
              <span className="mt-help">
                {hasOwnVoice
                  ? "Used only when the mentor's own voice is not available."
                  : "The mentor speaks in this voice."}
              </span>
              {form.voiceId && (
                <>
                  <label htmlFor="mentor-rate">Speed: {formatRate(form.rate)}</label>
                  <input
                    id="mentor-rate"
                    type="range"
                    min="-50"
                    max="50"
                    step="5"
                    value={form.rate}
                    onChange={(event) => set({ rate: Number(event.target.value) })}
                  />
                  <label htmlFor="mentor-pitch">Pitch: {formatPitch(form.pitch)}</label>
                  <input
                    id="mentor-pitch"
                    type="range"
                    min="-50"
                    max="50"
                    step="5"
                    value={form.pitch}
                    onChange={(event) => set({ pitch: Number(event.target.value) })}
                  />
                </>
              )}
            </div>

            <div className="mt-field">
              <label htmlFor="mentor-persona">Personality (optional)</label>
              <textarea
                id="mentor-persona"
                className="mt-input"
                rows={3}
                value={form.persona}
                maxLength={MAX_PERSONA_CHARS}
                onChange={(event) => set({ persona: event.target.value })}
                placeholder="How does this mentor talk? For example: calm and patient, short sentences, likes everyday comparisons."
              />
              <span className="mt-help">
                {form.persona.length}/{MAX_PERSONA_CHARS}
              </span>
              {errors.persona && <span className="mt-error">{errors.persona}</span>}
            </div>

            <div className="mt-field">
              <label htmlFor="mentor-expressiveness">Delivery of the own voice: {delivery}</label>
              <input
                id="mentor-expressiveness"
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={form.expressiveness}
                onChange={(event) => set({ expressiveness: Number(event.target.value) })}
              />
              <span className="mt-help">
                Balanced is the fastest. Calm or lively takes a little longer per sentence.
              </span>
            </div>

            {(!editing || form.images.length > 0 || form.voice) && (
              <div className="mt-field">
                <label className="flex items-start gap-2" style={{ fontWeight: 500 }}>
                  <input
                    type="checkbox"
                    checked={form.consent}
                    onChange={(event) => set({ consent: event.target.checked })}
                    className="mt-1"
                  />
                  <span>
                    I have permission to use this person’s face and voice for an AI mentor.
                  </span>
                </label>
                {errors.consent && <span className="mt-error">{errors.consent}</span>}
              </div>
            )}

            {submitError && (
              <p className="mt-error" role="alert">
                {submitError}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="submit"
                className="lw-button lw-button--primary"
                disabled={submitting || serviceDown}
              >
                {submitting ? "Saving…" : editing ? "Save changes" : "Create mentor"}
              </button>
              {editing && (
                <button type="button" className="lw-button" onClick={resetForm}>
                  Cancel
                </button>
              )}
            </div>
          </form>

          <aside className="mt-card" aria-label="Your mentors">
            <h2 className="text-base font-bold mb-2">Mentors</h2>
            {notice && (
              <p className="mt-help mb-2" role="status">
                {notice}
              </p>
            )}
            {!mentors.length && <p className="mt-help">No mentors yet.</p>}
            {mentors.map((mentor) => (
              <div className="mt-mentor-row" key={mentor.name}>
                <span className="mt-mentor-name">
                  {mentor.name}
                  <span
                    className="mt-help block"
                    style={{ textTransform: "none", fontWeight: 400 }}
                  >
                    {mentor.state === "failed"
                      ? mentor.detail
                      : `${mentor.voice === "cloned" ? "Own voice" : "Standard voice"}${mentor.persona ? " · personality set" : ""}`}
                  </span>
                </span>
                <span className="mt-badge" data-state={mentor.state}>
                  {STATE_LABEL[mentor.state] || mentor.state}
                </span>
                {mentor.uploaded && (
                  <>
                    <button
                      type="button"
                      className="lw-icon-button"
                      onClick={() => startEditing(mentor.name)}
                      aria-label={`Edit ${mentor.name}`}
                    >
                      <Pencil size={16} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="lw-icon-button"
                      onClick={() => remove(mentor.name)}
                      aria-label={`Remove ${mentor.name}`}
                    >
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  </>
                )}
              </div>
            ))}
          </aside>
        </div>
      </div>
    </div>
  );
}
