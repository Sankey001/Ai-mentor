// Client for the live mentor: conversation stream, mentor list, uploads and course content.

export const AI_SERVICE_URL = (
  import.meta.env.VITE_AI_SERVICE_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

export const MAX_IMAGES = 8;
export const MAX_IMAGE_BYTES = 15 * 1024 * 1024;
export const MAX_VOICE_BYTES = 30 * 1024 * 1024;
export const MAX_PERSONA_CHARS = 1000;
const MENTOR_NAME = /^[a-z0-9_-]{1,50}$/;

export const mediaUrl = (path) => `${AI_SERVICE_URL}${path}`;
export const idleClipUrl = (mentor) =>
  `${AI_SERVICE_URL}/mentors/${encodeURIComponent(mentor)}/idle.mp4`;

// A failed request carries the server's own explanation when it gave one.
const explain = async (response) => {
  let detail = "";
  try {
    const body = await response.json();
    detail = typeof body.detail === "string" ? body.detail : (body.detail?.[0]?.msg ?? "");
  } catch {
    // The response had no JSON body.
  }
  return new Error(detail || `The mentor service answered ${response.status}.`);
};

const getJson = async (path, signal) => {
  const response = await fetch(`${AI_SERVICE_URL}${path}`, { signal });
  if (!response.ok) throw await explain(response);
  return response.json();
};

const postJson = async (path, body, signal) => {
  const response = await fetch(`${AI_SERVICE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await explain(response);
  return response.json();
};

// How the service names a lesson. Prepared lessons are found by exactly these three
// values, so every call about a lesson builds them here.
export const lessonInfo = (course, lesson) => ({
  course: course?.title || "",
  module: lesson.moduleTitle || "",
  title: lesson.title,
});

// Prepared lessons ("lesson packs"): written, voiced and lip-synced ahead of time.
// `urgent` puts the lesson on screen and the next one before a whole course.
export const preparePacks = (mentor, lessons, { urgent = false } = {}) =>
  postJson("/lesson-packs/prepare", { mentor, lessons, urgent });
// { lessons: [{id, state, error}] in the order asked, ready, total, preparing, doing,
//   seconds_left, bytes }
export const packStatus = (mentor, lessons, signal) =>
  postJson("/lesson-packs/status", { mentor, lessons }, signal);
// { state, sentences: [{text, kind, url, voice, face}] } with sentences only when "ready"
export const getPack = (mentor, lesson) => postJson("/lesson-packs/pack", { mentor, lesson });
export const stopPreparing = async (mentor) => {
  const response = await fetch(
    `${AI_SERVICE_URL}/lesson-packs/${encodeURIComponent(mentor)}/queue`,
    { method: "DELETE" },
  );
  if (!response.ok) throw await explain(response);
};

export const formatTimeLeft = (seconds) =>
  seconds < 90 ? "about a minute" : `about ${Math.round(seconds / 60)} minutes`;
export const formatSize = (bytes) =>
  bytes < 1e9 ? `${Math.max(1, Math.round(bytes / 1e6))} MB` : `${(bytes / 1e9).toFixed(1)} GB`;

export const listMentors = async (signal) => (await getJson("/mentors", signal)).mentors;
export const workerStatus = (signal) => getJson("/talk/status", signal);
// { default: {source, provider, model}, options: [{source, provider, model, label, available}] }
export const listModels = (signal) => getJson("/talk/models", signal);

// Split a Server-Sent Events buffer into parsed events and the unfinished tail.
export const parseSseEvents = (buffer) => {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop();
  const events = [];
  for (const block of blocks) {
    if (!block.startsWith("data: ")) continue;
    try {
      events.push(JSON.parse(block.slice(6)));
    } catch {
      // A damaged event is skipped; the next ones still arrive.
    }
  }
  return { events, rest };
};

// Send one turn to the mentor and report each event as it arrives.
export const streamTalk = async ({
  mentor,
  message = "",
  lesson,
  history = [],
  provider,
  model,
  think = false,
  signal,
  onEvent,
}) => {
  const response = await fetch(`${AI_SERVICE_URL}/talk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mentor,
      message,
      ...(lesson && { lesson }),
      history,
      // The learner's pick from listModels; omitted, the service uses its default.
      ...(provider && { provider, model }),
      // Only for a model whose listModels entry says `thinking: true`.
      ...(think && { think: true }),
    }),
    signal,
  });
  if (!response.ok) throw await explain(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseSseEvents(buffer + decoder.decode(value, { stream: true }));
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
};

export const listVoices = async (signal) => (await getJson("/voices", signal)).voices;

// Settings page: API keys come back only as { set, hint }, never their value.
export const getSettings = (signal) => getJson("/settings", signal);
export const saveSettings = async (changes) => {
  const response = await fetch(`${AI_SERVICE_URL}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes }),
  });
  if (!response.ok) throw await explain(response);
  return response.json();
};
export const getMentor = (name, signal) => getJson(`/mentors/${encodeURIComponent(name)}`, signal);
export const mentorImageUrl = (name, filename) =>
  `${AI_SERVICE_URL}/mentors/${encodeURIComponent(name)}/images/${encodeURIComponent(filename)}`;

// Speed and pitch travel as signed text ("+10%", "-5Hz"); the sliders work in plain numbers.
const signed = (value) => `${value < 0 ? "-" : "+"}${Math.abs(Math.round(value))}`;
export const formatRate = (value) => `${signed(value)}%`;
export const formatPitch = (value) => `${signed(value)}Hz`;
export const parseAdjustment = (text) => Number.parseInt(text, 10) || 0;

// `existingImages` is how many saved photos an edited mentor keeps; omit it when creating.
export const validateMentorForm = (
  { name, persona, images, voice, consent },
  { existingImages } = {},
) => {
  const editing = existingImages !== undefined;
  const errors = {};
  if (!MENTOR_NAME.test(name || "")) {
    errors.name = "Use lowercase letters, digits, hyphens or underscores (up to 50).";
  }
  if ((persona || "").length > MAX_PERSONA_CHARS) {
    errors.persona = `Keep the personality under ${MAX_PERSONA_CHARS} characters.`;
  }
  const total = (existingImages || 0) + (images?.length || 0);
  if (total < 1) {
    errors.images = "Add at least one image of the mentor's face.";
  } else if (total > MAX_IMAGES) {
    errors.images = `Add at most ${MAX_IMAGES} images.`;
  } else if (images.some((image) => image.size > MAX_IMAGE_BYTES)) {
    errors.images = "Each image must be smaller than 15 MB.";
  }
  if (voice && voice.size > MAX_VOICE_BYTES) {
    errors.voice = "The voice recording must be smaller than 30 MB.";
  }
  if (!consent && (!editing || images?.length || voice)) {
    errors.consent = "Confirm that you have permission to use this face and voice.";
  }
  return errors;
};

const mentorForm = ({ persona, expressiveness, images, voice, consent, voiceId, rate, pitch }) => {
  const form = new FormData();
  form.append("persona", persona);
  form.append("expressiveness", String(expressiveness));
  form.append("consent", String(Boolean(consent)));
  form.append("voice_id", voiceId || "");
  if (voiceId) {
    form.append("speech_rate", formatRate(rate || 0));
    form.append("speech_pitch", formatPitch(pitch || 0));
  }
  (images || []).forEach((image) => form.append("images", image, image.name));
  if (voice) form.append("voice", voice, voice.name || "voice.webm");
  return form;
};

export const createMentor = async (values) => {
  const form = mentorForm(values);
  form.append("name", values.name);
  const response = await fetch(`${AI_SERVICE_URL}/mentors`, { method: "POST", body: form });
  if (!response.ok) throw await explain(response);
  return response.json();
};

export const updateMentor = async (name, values) => {
  const form = mentorForm(values);
  (values.removeImages || []).forEach((filename) => form.append("remove_images", filename));
  if (values.removeVoice) form.append("remove_voice", "true");
  const response = await fetch(`${AI_SERVICE_URL}/mentors/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: form,
  });
  if (!response.ok) throw await explain(response);
  return response.json();
};

export const deleteMentor = async (name) => {
  const response = await fetch(`${AI_SERVICE_URL}/mentors/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await explain(response);
};

export const flattenLessons = (modules = []) =>
  modules.flatMap((module) =>
    (module.lessons || []).map((lesson) => ({ ...lesson, moduleTitle: module.title })),
  );

// Course chapters come from the course API; without it, from the bundled sample course.
export const loadCourseContent = async (courseId, { token, signal } = {}) => {
  if (token) {
    try {
      const response = await fetch(`/api/courses/${courseId}/learning`, {
        headers: { Authorization: `Bearer ${token}` },
        signal,
      });
      if (response.ok) {
        const data = await response.json();
        if (data.modules?.length) {
          return {
            title: data.title || data.courseTitle || "",
            modules: data.modules,
            source: "api",
          };
        }
      }
    } catch (error) {
      if (error.name === "AbortError") throw error;
      // The API is unreachable; the sample course below still works.
    }
  }

  const [learning, courses] = await Promise.all([
    fetch("/data/learning.json", { signal }).then((response) => response.json()),
    fetch("/data/courses.json", { signal })
      .then((response) => response.json())
      .catch(() => ({})),
  ]);
  const modules = learning?.[courseId]?.modules;
  if (!modules?.length) throw new Error("This course has no lessons yet.");
  const card = (courses.courseCards || []).find((course) => String(course.id) === String(courseId));
  return { title: card?.title || "", modules, source: "sample" };
};
