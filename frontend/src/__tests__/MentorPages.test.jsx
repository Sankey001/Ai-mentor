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
  workerStatus: vi.fn(),
  loadCourseContent: vi.fn(),
  streamTalk: vi.fn(),
  createMentor: vi.fn(),
  deleteMentor: vi.fn(),
  updateMentor: vi.fn(),
  getMentor: vi.fn(),
  listVoices: vi.fn(),
}));
vi.mock("../service/mentorLive", async (importActual) => ({
  ...(await importActual()),
  ...service,
}));

import MentorClassroom from "../pages/MentorClassroom";
import MentorStudio from "../pages/MentorStudio";

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

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/mentor" element={<MentorClassroom />} />
        <Route path="/mentor/:courseId" element={<MentorClassroom />} />
        <Route path="/mentor-studio" element={<MentorStudio />} />
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
    expect(screen.getByText("Voice: cloned · Face: lipsync")).toBeTruthy();
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
    await screen.findByText("Voice: cloned · Face: lipsync");
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
