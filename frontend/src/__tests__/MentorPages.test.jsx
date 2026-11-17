import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import enLocales from "../i18n/locales/en.json";

// The real pages and the real lesson picker are rendered. Only the outer
// boundary is replaced: the calls to the mentor service and course API.

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key) => key.split(".").reduce((acc, part) => acc?.[part], enLocales) || key,
    i18n: { language: "en" },
  }),
}));

const service = vi.hoisted(() => ({
  listMentors: vi.fn(),
  listModels: vi.fn(),
  workerStatus: vi.fn(),
  loadCourseContent: vi.fn(),
  streamTalk: vi.fn(),
  preparePacks: vi.fn(),
  packStatus: vi.fn(),
  getPack: vi.fn(),
  stopPreparing: vi.fn(),
  createMentor: vi.fn(),
  deleteMentor: vi.fn(),
  updateMentor: vi.fn(),
  getMentor: vi.fn(),
  listVoices: vi.fn(),
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
}));
vi.mock("../service/mentorLive", async (importActual) => ({
  ...(await importActual()),
  ...service,
}));

import MentorClassroom from "../pages/MentorClassroom";
import MentorStudio from "../pages/MentorStudio";
import MentorSettings from "../pages/MentorSettings";

const course = {
  title: "React Fundamentals",
  source: "sample",
  modules: [
    {
      id: "m1",
      title: "Module 1: Introduction to React",
      lessons: [
        { id: "what-is-react", title: "What is React?", type: "video" },
        { id: "history", title: "History of React", type: "video" },
      ],
    },
    { id: "m2", title: "Module 2: Components", lessons: [{ id: "props", title: "Props Basics" }] },
  ],
};
const mentors = [
  { name: "asha", voice: "cloned", persona: true, state: "ready", uploaded: true },
  { name: "bo", voice: "stock", persona: false, state: "preparing", uploaded: true },
  { name: "owl", voice: "stock", persona: false, state: "ready", uploaded: false },
];

const models = {
  default: { source: "api", provider: "gemini", model: "gemini-2.5-flash" },
  options: [
    {
      source: "local",
      provider: "ollama",
      model: "gemma3:latest",
      label: "gemma3:latest (3.3 GB)",
      available: true,
    },
    {
      source: "local",
      provider: "ollama",
      model: "qwen3.5:9b",
      label: "qwen3.5:9b (6.6 GB)",
      available: true,
      thinking: true,
    },
    {
      source: "api",
      provider: "gemini",
      model: "gemini-2.5-flash",
      label: "Gemini (gemini-2.5-flash)",
      available: true,
    },
    {
      source: "api",
      provider: "groq",
      model: "llama-3.3-70b-versatile",
      label: "Groq (Llama 3.3 70B)",
      available: false,
    },
  ],
};

// What the service says about the course's three prepared lessons ("lesson packs").
const packsStatus = (states = ["missing", "missing", "missing"], extra = {}) => ({
  lessons: states.map((state, index) => ({ id: `lesson-${index}`, state, error: "" })),
  ready: states.filter((state) => state === "ready").length,
  total: states.length,
  preparing: states.filter((state) => state === "preparing").length,
  doing: "",
  seconds_left: 0,
  bytes: 0,
  ...extra,
});
const sentence = (text, n) => ({
  text,
  kind: "video",
  voice: "cloned",
  face: "lipsync",
  url: `/pack-media/asha/${n}.mp4`,
});
const preparedLesson = {
  state: "ready",
  sentences: [
    sentence("React is a toolkit.", 0),
    sentence("It builds screens.", 1),
    sentence("That is React.", 2),
  ],
};
const talkingVideo = () => screen.getByLabelText("asha speaking");
const reactLessons = [
  {
    course: "React Fundamentals",
    module: "Module 1: Introduction to React",
    title: "What is React?",
  },
  {
    course: "React Fundamentals",
    module: "Module 1: Introduction to React",
    title: "History of React",
  },
  { course: "React Fundamentals", module: "Module 2: Components", title: "Props Basics" },
];

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/mentor" element={<MentorClassroom />} />
        <Route path="/mentor/:courseId" element={<MentorClassroom />} />
        <Route path="/mentor-studio" element={<MentorStudio />} />
        <Route path="/mentor-settings" element={<MentorSettings />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  localStorage.clear();
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  window.HTMLMediaElement.prototype.pause = vi.fn();
  window.URL.createObjectURL = vi.fn(() => "blob:preview");
  window.URL.revokeObjectURL = vi.fn();
  service.listMentors.mockResolvedValue(mentors);
  service.listModels.mockResolvedValue(models);
  service.workerStatus.mockResolvedValue({ voice: true, face: false });
  service.loadCourseContent.mockResolvedValue(course);
  service.streamTalk.mockImplementation(async ({ onEvent }) => {
    onEvent({ type: "reply", text: "React builds screens from small parts.", provider: "gemini" });
    onEvent({
      type: "chunk",
      index: 0,
      text: "React builds screens from small parts.",
      kind: "video",
      voice: "cloned",
      face: "lipsync",
      url: "/talk-media/s/0.mp4",
    });
    onEvent({ type: "done" });
  });
  // No lesson is prepared unless a test says so.
  service.packStatus.mockResolvedValue(packsStatus());
  service.preparePacks.mockResolvedValue(packsStatus());
  service.getPack.mockResolvedValue({ state: "missing" });
  service.stopPreparing.mockResolvedValue();
  service.createMentor.mockResolvedValue({ name: "asha", state: "preparing" });
  service.deleteMentor.mockResolvedValue();
  service.updateMentor.mockResolvedValue({ name: "asha", state: "ready" });
  service.listVoices.mockResolvedValue([
    {
      voice_id: "en-GB-RyanNeural",
      name: "Ryan",
      gender: "Male",
      language: "English",
      accent: "UK",
    },
    {
      voice_id: "en-IN-NeerjaNeural",
      name: "Neerja",
      gender: "Female",
      language: "English",
      accent: "India",
    },
  ]);
  service.getMentor.mockResolvedValue({
    name: "asha",
    persona: "Kind and patient.",
    expressiveness: 0.7,
    voice: { voice_id: "en-GB-RyanNeural", rate: "+10%", pitch: "-5Hz" },
    own_voice: true,
    uploaded: true,
    images: ["image_1.png", "image_2.webp"],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MentorClassroom", () => {
  it("shows the course chapters and only the mentors that are ready", async () => {
    renderAt("/mentor/1");

    expect(await screen.findByRole("heading", { name: "What is React?" })).toBeTruthy();
    expect(screen.getByText(/LESSON 1 \/ 3/)).toBeTruthy();
    expect(service.loadCourseContent).toHaveBeenCalledWith("1", expect.any(Object));
    const select = await screen.findByLabelText("Mentor");
    await waitFor(() => expect(select.value).toBe("asha"));
    expect([...select.options].map((option) => option.value)).toEqual(["asha", "owl"]);
    expect(screen.getByText("Mentor's own voice")).toBeTruthy();
    expect(screen.getByText("Lip-sync off")).toBeTruthy();
  });

  it("teaches the selected lesson and plays the mentor's reply", async () => {
    renderAt("/mentor/1");
    await screen.findByRole("heading", { name: "What is React?" });
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    fireEvent.click(screen.getByRole("button", { name: /Teach this lesson/ }));

    expect((await screen.findAllByText("React builds screens from small parts.")).length).toBe(2);
    expect(screen.getByText("Teach me: What is React?")).toBeTruthy();
    expect(screen.getByText("AI: gemini · Voice: cloned · Face: lipsync")).toBeTruthy();
    const call = service.streamTalk.mock.calls[0][0];
    expect(call.mentor).toBe("asha");
    expect(call.message).toBe("");
    expect(call.lesson).toEqual({
      course: "React Fundamentals",
      module: "Module 1: Introduction to React",
      title: "What is React?",
    });
    expect(window.HTMLMediaElement.prototype.play).toHaveBeenCalled();
  });

  it("moves between lessons and sends typed questions with the lesson and the history", async () => {
    renderAt("/mentor/1");
    await screen.findByRole("heading", { name: "What is React?" });
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    fireEvent.click(screen.getByRole("button", { name: "Next lesson" }));
    expect(screen.getByRole("heading", { name: "History of React" })).toBeTruthy();

    const input = screen.getByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Who made it?" } });
    fireEvent.submit(input.closest("form"));
    await screen.findByText("AI: gemini · Voice: cloned · Face: lipsync");
    fireEvent.change(input, { target: { value: "And when?" } });
    fireEvent.submit(input.closest("form"));

    await waitFor(() => expect(service.streamTalk).toHaveBeenCalledTimes(2));
    const second = service.streamTalk.mock.calls[1][0];
    expect(second.message).toBe("And when?");
    expect(second.lesson.title).toBe("History of React");
    expect(second.history).toEqual([
      { role: "learner", text: "Who made it?" },
      { role: "mentor", text: "React builds screens from small parts." },
    ]);
  });

  it("lets the learner run replies on this computer or an online API, and remembers it", async () => {
    renderAt("/mentor/1");
    await screen.findByRole("heading", { name: "What is React?" });
    const source = screen.getByLabelText("Run on");
    const model = screen.getByLabelText("AI model");
    // Without a pick, the service's default (an online API here) is shown.
    await waitFor(() => expect(source.value).toBe("api"));
    expect(model.value).toBe("gemini|gemini-2.5-flash");
    const groq = [...model.options].find((option) => option.value.startsWith("groq"));
    expect(groq.disabled).toBe(true);
    expect(groq.textContent).toMatch(/no API key/);

    fireEvent.change(source, { target: { value: "local" } });
    expect(model.value).toBe("ollama|gemma3:latest");
    fireEvent.change(model, { target: { value: "ollama|qwen3.5:9b" } });
    expect(JSON.parse(localStorage.getItem("ai-mentor-live-model"))).toEqual({
      provider: "ollama",
      model: "qwen3.5:9b",
    });

    service.streamTalk.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: "reply", text: "Local answer.", provider: "ollama" });
      onEvent({ type: "done" });
    });
    const input = screen.getByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Hi" } });
    fireEvent.submit(input.closest("form"));

    await screen.findByText(/AI: qwen3.5:9b/);
    const call = service.streamTalk.mock.calls[0][0];
    expect([call.provider, call.model]).toEqual(["ollama", "qwen3.5:9b"]);
  });

  it("offers Think first only for a thinking model and sends it with the question", async () => {
    renderAt("/mentor/1");
    await screen.findByRole("heading", { name: "What is React?" });
    await waitFor(() => expect(screen.getByLabelText("Run on").value).toBe("api"));
    const toggle = screen.getByRole("button", { name: /Think first/ });
    // Gemini (the default here) cannot think, nor can gemma3.
    expect(toggle.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Run on"), { target: { value: "local" } });
    expect(toggle.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("AI model"), { target: { value: "ollama|qwen3.5:9b" } });
    expect(toggle.disabled).toBe(false);
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(localStorage.getItem("ai-mentor-live-think")).toBe("true");

    service.streamTalk.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: "reply", text: "Thought it through.", provider: "ollama" });
      onEvent({ type: "done" });
    });
    const input = screen.getByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Why?" } });
    fireEvent.submit(input.closest("form"));

    await screen.findByText(/AI: qwen3.5:9b \(thinking\)/);
    expect(service.streamTalk.mock.calls[0][0].think).toBe(true);

    // Switching to a model that cannot think stops sending it, but keeps the setting.
    fireEvent.change(screen.getByLabelText("AI model"), {
      target: { value: "ollama|gemma3:latest" },
    });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.change(input, { target: { value: "And?" } });
    fireEvent.submit(input.closest("form"));
    await waitFor(() => expect(service.streamTalk).toHaveBeenCalledTimes(2));
    expect(service.streamTalk.mock.calls[1][0].think).toBe(false);
    expect(localStorage.getItem("ai-mentor-live-think")).toBe("true");
  });

  it("still talks with the service default when the model list cannot load", async () => {
    service.listModels.mockRejectedValue(new TypeError("Failed to fetch"));
    renderAt("/mentor/1");
    await screen.findByRole("heading", { name: "What is React?" });
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    expect(screen.getByLabelText("AI model").disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Teach this lesson/ }));

    await screen.findByText("AI: gemini · Voice: cloned · Face: lipsync");
    expect(service.streamTalk.mock.calls[0][0].provider).toBeUndefined();
  });

  it("explains what to do when the mentor service is down", async () => {
    service.listMentors.mockRejectedValue(new TypeError("Failed to fetch"));
    renderAt("/mentor");

    expect((await screen.findByRole("alert")).textContent).toMatch(/not reachable/);
    expect(screen.getByRole("button", { name: /Teach this lesson/ }).disabled).toBe(true);
  });

  it("shows a clear message when the mentor cannot answer", async () => {
    service.streamTalk.mockRejectedValue(new Error("Mentor not found"));
    renderAt("/mentor/1");
    await screen.findByRole("heading", { name: "What is React?" });
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    fireEvent.click(screen.getByRole("button", { name: "Quiz me on this" }));

    expect(await screen.findByText(/could not answer. Mentor not found/)).toBeTruthy();
  });

  it("shows the reply while the mentor is still writing it", async () => {
    let finish;
    service.streamTalk.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: "text", text: "Loops repeat code." });
      await new Promise((resolve) => (finish = resolve));
      onEvent({ type: "reply", text: "Loops repeat code. They save typing!", provider: "ollama" });
    });
    renderAt("/mentor/1");
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    fireEvent.click(screen.getByRole("button", { name: "Quiz me on this" }));

    expect(await screen.findByText("Loops repeat code.")).toBeTruthy();
    finish();
    expect(await screen.findByText("Loops repeat code. They save typing!")).toBeTruthy();
  });

  it("prepares the open lesson, then the ones after it, and the whole course on request", async () => {
    service.packStatus.mockResolvedValue(packsStatus(["ready", "missing", "missing"]));
    renderAt("/mentor/1");
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    // The open lesson first, then up to five that follow it (this course has two more).
    await waitFor(() =>
      expect(service.preparePacks.mock.calls.slice(0, 2)).toEqual([
        ["asha", reactLessons.slice(0, 1), { urgent: true }],
        ["asha", reactLessons.slice(1, 3)],
      ]),
    );
    expect(await screen.findByText(/1 of 3 lessons prepared/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Teach this lesson/ }).textContent).toMatch(
      /Ready to play/,
    );

    service.preparePacks.mockResolvedValue(
      packsStatus(["ready", "preparing", "preparing"], {
        seconds_left: 240,
        bytes: 7_000_000,
        doing: "Writing “History of React”",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Prepare whole course/ }));

    await waitFor(() =>
      expect(service.preparePacks).toHaveBeenLastCalledWith("asha", reactLessons),
    );
    expect(await screen.findByText(/2 to go, about 4 minutes left/)).toBeTruthy();
    expect(screen.getByText(/7 MB on disk/)).toBeTruthy();
    expect(screen.getByText("Writing “History of React”")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Stop preparing/ }));
    await waitFor(() => expect(service.stopPreparing).toHaveBeenCalledWith("asha"));
  });

  it("plays a prepared lesson at once, pauses it for a question, then goes on", async () => {
    service.getPack.mockResolvedValue(preparedLesson);
    renderAt("/mentor/1");
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));

    fireEvent.click(screen.getByRole("button", { name: /Teach this lesson/ }));

    // The lesson plays from its files: the AI is not asked for anything.
    expect(
      await screen.findByText("React is a toolkit. It builds screens. That is React."),
    ).toBeTruthy();
    expect(screen.getByText("Prepared lesson · Voice: cloned · Face: lipsync")).toBeTruthy();
    expect(service.getPack).toHaveBeenCalledWith("asha", reactLessons[0]);
    expect(service.streamTalk).not.toHaveBeenCalled();
    expect(talkingVideo().src).toMatch(/\/pack-media\/asha\/0\.mp4$/);
    fireEvent.ended(talkingVideo());
    expect(talkingVideo().src).toMatch(/\/pack-media\/asha\/1\.mp4$/);

    // A question during sentence 2 interrupts it and is answered live.
    const input = screen.getByLabelText("Your question");
    fireEvent.change(input, { target: { value: "Why screens?" } });
    fireEvent.submit(input.closest("form"));
    await screen.findByText("AI: gemini · Voice: cloned · Face: lipsync");
    expect(service.streamTalk.mock.calls[0][0].message).toBe("Why screens?");
    expect(service.streamTalk.mock.calls[0][0].lesson).toEqual(reactLessons[0]);
    expect(talkingVideo().src).toMatch(/\/talk-media\/s\/0\.mp4$/);

    // Once the answer has been spoken, the lesson goes on with sentence 3.
    fireEvent.ended(talkingVideo());
    await waitFor(() => expect(talkingVideo().src).toMatch(/\/pack-media\/asha\/2\.mp4$/));
    fireEvent.ended(talkingVideo());
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  });

  it("stops a prepared lesson when the learner moves to another chapter", async () => {
    service.getPack.mockResolvedValue(preparedLesson);
    renderAt("/mentor/1");
    await waitFor(() => expect(screen.getByLabelText("Mentor").value).toBe("asha"));
    fireEvent.click(screen.getByRole("button", { name: /Teach this lesson/ }));
    await screen.findByText("Prepared lesson · Voice: cloned · Face: lipsync");

    fireEvent.click(screen.getByRole("button", { name: "Next lesson" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    expect(window.HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });
});

describe("MentorSettings", () => {
  const settings = {
    env_file: "E:\\Ai-mentor\\ai_service\\backend\\.env",
    values: {
      AI_PROVIDER: { value: "ollama" },
      OLLAMA_MODEL: { value: "gemma3:latest" },
      OLLAMA_URL: { value: "http://127.0.0.1:11434" },
      GEMINI_MODEL: { value: "gemini-2.5-flash" },
      GEMINI_API_KEY: { set: true, hint: "abcd" },
      GROQ_API_KEY: { set: false, hint: "" },
      MUSETALK_DIR: { value: "E:/workers/MuseTalk", exists: true },
      FACE_WORKER_PYTHON: { value: "E:/workers/face/python.exe", exists: true },
      VOICE_WORKER_PYTHON: { value: "", exists: false },
    },
    ollama_models: {
      value: "D:\\Ollama_Models",
      source: "Set for this Windows user",
      exists: true,
      editable: true,
      installed: ["gemma3:latest", "qwen3.5:9b"],
    },
    huggingface: { value: "E:\\ai-models\\huggingface", exists: true },
  };

  it("shows where models live, hides saved keys, and saves only what changed", async () => {
    service.getSettings.mockResolvedValue(settings);
    service.saveSettings.mockResolvedValue(settings);
    renderAt("/mentor-settings");

    const folder = await screen.findByLabelText(/Local AI models folder/);
    expect(folder.value).toBe("D:\\Ollama_Models");
    expect(screen.getByText(/serving 2 models: gemma3:latest, qwen3.5:9b/)).toBeTruthy();
    expect(screen.getByText("Saved …abcd")).toBeTruthy();
    expect(screen.getByLabelText(/Gemini API key/).value).toBe("");
    const save = screen.getByRole("button", { name: /Save settings/ });
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Default local model"), {
      target: { value: "qwen3.5:9b" },
    });
    fireEvent.change(screen.getByLabelText(/Groq API key/), { target: { value: "gsk_123" } });
    fireEvent.click(save);

    await screen.findByText(/Saved\. Restart the app/);
    expect(service.saveSettings).toHaveBeenCalledWith({
      OLLAMA_MODEL: "qwen3.5:9b",
      GROQ_API_KEY: "gsk_123",
    });
  });

  it("removes a saved key only when asked, and shows the server's reason on failure", async () => {
    service.getSettings.mockResolvedValue(settings);
    service.saveSettings.mockRejectedValue(new Error("OLLAMA_URL must look like http://..."));
    renderAt("/mentor-settings");

    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(screen.getByText("Will be removed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Save settings/ }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/OLLAMA_URL must look like/);
    expect(service.saveSettings).toHaveBeenCalledWith({ GEMINI_API_KEY: "" });
  });
});

describe("MentorStudio", () => {
  const fill = async () => {
    fireEvent.change(screen.getByLabelText("Mentor name"), { target: { value: " Asha " } });
    fireEvent.change(screen.getByTestId("image-input"), {
      target: { files: [new File(["a"], "front.heic"), new File(["b"], "side.webp")] },
    });
    fireEvent.change(screen.getByTestId("voice-input"), {
      target: { files: [new File(["v"], "me.m4a")] },
    });
    fireEvent.change(screen.getByLabelText(/Personality/), { target: { value: "Calm." } });
    fireEvent.click(screen.getByRole("checkbox"));
  };

  it("lists mentors with their state and removes only uploaded ones", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderAt("/mentor-studio");

    expect(await screen.findByText("Preparing")).toBeTruthy();
    expect(screen.getAllByText("Ready")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Remove owl" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Remove asha" }));
    await waitFor(() => expect(service.deleteMentor).toHaveBeenCalledWith("asha"));
  });

  it("explains what is missing and does not upload an incomplete form", async () => {
    renderAt("/mentor-studio");
    fireEvent.click(await screen.findByRole("button", { name: "Create mentor" }));

    expect(screen.getByText(/Add at least one image/)).toBeTruthy();
    expect(screen.getByText(/Confirm that you have permission/)).toBeTruthy();
    expect(service.createMentor).not.toHaveBeenCalled();
  });

  it("uploads the photos, voice and personality", async () => {
    renderAt("/mentor-studio");
    await screen.findByRole("button", { name: "Create mentor" });
    await fill();
    expect(screen.getByText(/Photos of the mentor \(2\/8\)/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Create mentor" }));

    await waitFor(() => expect(service.createMentor).toHaveBeenCalledTimes(1));
    const sent = service.createMentor.mock.calls[0][0];
    expect(sent.name).toBe("asha");
    expect(sent.persona).toBe("Calm.");
    expect(sent.consent).toBe(true);
    expect(sent.images.map((file) => file.name)).toEqual(["front.heic", "side.webp"]);
    expect(sent.voice.name).toBe("me.m4a");
    expect(await screen.findByText(/being prepared/)).toBeTruthy();
  });

  it("shows the server's reason when an upload is refused", async () => {
    service.createMentor.mockRejectedValue(new Error("A mentor with this name already exists."));
    renderAt("/mentor-studio");
    await screen.findByRole("button", { name: "Create mentor" });
    await fill();

    fireEvent.click(screen.getByRole("button", { name: "Create mentor" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/already exists/);
  });

  it("lets a new mentor use a chosen standard voice with speed and pitch", async () => {
    renderAt("/mentor-studio");
    await screen.findByRole("option", { name: /Neerja/ });
    await fill();

    fireEvent.change(screen.getByLabelText("Standard voice"), {
      target: { value: "en-IN-NeerjaNeural" },
    });
    fireEvent.change(screen.getByLabelText(/Speed/), { target: { value: "-10" } });
    fireEvent.change(screen.getByLabelText(/Pitch/), { target: { value: "5" } });
    expect(screen.getByText("Speed: -10%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create mentor" }));

    await waitFor(() => expect(service.createMentor).toHaveBeenCalledTimes(1));
    const sent = service.createMentor.mock.calls[0][0];
    expect([sent.voiceId, sent.rate, sent.pitch]).toEqual(["en-IN-NeerjaNeural", -10, 5]);
  });

  it("loads a mentor for editing and saves only what changed", async () => {
    renderAt("/mentor-studio");
    fireEvent.click(await screen.findByRole("button", { name: "Edit asha" }));

    expect(await screen.findByRole("heading", { name: "Edit “asha”" })).toBeTruthy();
    expect(screen.queryByLabelText("Mentor name")).toBeNull();
    expect(screen.getByLabelText(/Personality/).value).toBe("Kind and patient.");
    expect(screen.getByLabelText("Standard voice").value).toBe("en-GB-RyanNeural");
    expect(screen.getByText("Speed: +10%")).toBeTruthy();
    expect(screen.getByText("Pitch: -5Hz")).toBeTruthy();
    expect(screen.getByText(/Photos of the mentor \(2\/8\)/)).toBeTruthy();
    expect(screen.getByText("This mentor has a saved voice recording.")).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();

    fireEvent.change(screen.getByLabelText(/Personality/), { target: { value: "Brisk." } });
    fireEvent.click(screen.getByRole("button", { name: "Remove image_1.png" }));
    expect(screen.getByText(/Photos of the mentor \(1\/8\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove the saved recording" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(service.updateMentor).toHaveBeenCalledTimes(1));
    const [name, sent] = service.updateMentor.mock.calls[0];
    expect(name).toBe("asha");
    expect(sent.persona).toBe("Brisk.");
    expect(sent.removeImages).toEqual(["image_1.png"]);
    expect(sent.removeVoice).toBe(true);
    expect(sent.images).toEqual([]);
    expect(await screen.findByText("“asha” was saved.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Create a mentor/ })).toBeTruthy();
  });

  it("does not let the last photo be removed, and asks permission for new files", async () => {
    renderAt("/mentor-studio");
    fireEvent.click(await screen.findByRole("button", { name: "Edit asha" }));
    await screen.findByRole("heading", { name: "Edit “asha”" });

    fireEvent.click(screen.getByRole("button", { name: "Remove image_1.png" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove image_2.webp" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByText(/Add at least one image/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Keep image_2.webp" }));
    fireEvent.change(screen.getByTestId("image-input"), {
      target: { files: [new File(["n"], "new.png")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByText(/Confirm that you have permission/)).toBeTruthy();
    expect(service.updateMentor).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("heading", { name: /Create a mentor/ })).toBeTruthy();
  });
});
