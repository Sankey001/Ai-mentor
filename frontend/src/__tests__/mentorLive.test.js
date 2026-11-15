import { describe, it, expect, vi, afterEach } from "vitest";
import {
  AI_SERVICE_URL,
  createMentor,
  formatPitch,
  formatRate,
  parseAdjustment,
  updateMentor,
  flattenLessons,
  loadCourseContent,
  mediaUrl,
  parseSseEvents,
  streamTalk,
  validateMentorForm,
} from "../service/mentorLive";

const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const streamResponse = (parts) =>
  new Response(
    new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        parts.forEach((part) => controller.enqueue(encoder.encode(part)));
        controller.close();
      },
    }),
    { status: 200 },
  );

afterEach(() => vi.unstubAllGlobals());

describe("parseSseEvents", () => {
  it("returns complete events and keeps the unfinished tail", () => {
    const { events, rest } = parseSseEvents('data: {"type":"reply"}\n\ndata: {"type":"ch');
    expect(events).toEqual([{ type: "reply" }]);
    expect(rest).toBe('data: {"type":"ch');
  });

  it("skips lines that are not data and events that are not JSON", () => {
    const { events } = parseSseEvents(': ping\n\ndata: not json\n\ndata: {"type":"done"}\n\n');
    expect(events).toEqual([{ type: "done" }]);
  });
});

describe("streamTalk", () => {
  it("posts the turn and reports events even when they arrive split across packets", async () => {
    const fetchMock = vi.fn(async () =>
      streamResponse([
        'data: {"type":"reply","text":"Hi"}\n\ndata: {"type":"chu',
        'nk","index":0}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const events = [];

    await streamTalk({
      mentor: "asha",
      message: "hello",
      lesson: { course: "React", module: "M1", title: "What is React?" },
      history: [{ role: "learner", text: "earlier" }],
      onEvent: (event) => events.push(event),
    });

    expect(events).toEqual([
      { type: "reply", text: "Hi" },
      { type: "chunk", index: 0 },
    ]);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${AI_SERVICE_URL}/talk`);
    expect(JSON.parse(options.body)).toEqual({
      mentor: "asha",
      message: "hello",
      lesson: { course: "React", module: "M1", title: "What is React?" },
      history: [{ role: "learner", text: "earlier" }],
    });
  });

  it("throws when the service refuses the request", async () => {
    vi.stubGlobal("fetch", async () => jsonResponse({ detail: "Mentor not found" }, 404));
    await expect(streamTalk({ mentor: "x", message: "hi", onEvent: () => {} })).rejects.toThrow(
      "Mentor not found",
    );
  });
});

describe("loadCourseContent", () => {
  const sample = {
    1: { modules: [{ id: "m1", title: "Module 1", lessons: [{ id: "a", title: "A" }] }] },
  };
  const courses = { courseCards: [{ id: 1, title: "React Fundamentals" }] };

  it("uses the course API when the learner is signed in", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ modules: sample[1].modules, title: "From API" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const course = await loadCourseContent("1", { token: "abc" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/courses/1/learning");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer abc");
    expect(course).toEqual({ title: "From API", modules: sample[1].modules, source: "api" });
  });

  it("falls back to the bundled sample course when the API is unavailable", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url.startsWith("/api/")) throw new TypeError("Failed to fetch");
      return jsonResponse(url.endsWith("learning.json") ? sample : courses);
    });
    vi.stubGlobal("fetch", fetchMock);

    const course = await loadCourseContent("1", { token: "abc" });

    expect(course).toEqual({
      title: "React Fundamentals",
      modules: sample[1].modules,
      source: "sample",
    });
  });

  it("skips the API without a token and reports an unknown course", async () => {
    const fetchMock = vi.fn(async (url) =>
      jsonResponse(url.endsWith("learning.json") ? sample : courses),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadCourseContent("9", {})).rejects.toThrow();
    expect(fetchMock.mock.calls.every(([url]) => !url.startsWith("/api/"))).toBe(true);
  });
});

describe("helpers", () => {
  it("flattens lessons and keeps their module title", () => {
    const lessons = flattenLessons([
      { title: "M1", lessons: [{ id: "a", title: "A" }] },
      { title: "M2", lessons: [{ id: "b", title: "B" }] },
      { title: "Empty" },
    ]);
    expect(lessons).toEqual([
      { id: "a", title: "A", moduleTitle: "M1" },
      { id: "b", title: "B", moduleTitle: "M2" },
    ]);
  });

  it("builds media URLs on the AI service", () => {
    expect(mediaUrl("/talk-media/a/0.mp4")).toBe(`${AI_SERVICE_URL}/talk-media/a/0.mp4`);
  });
});

describe("validateMentorForm", () => {
  const image = new File(["x"], "a.png");
  const valid = { name: "asha_1", persona: "", images: [image], voice: null, consent: true };

  it("accepts a complete form", () => {
    expect(validateMentorForm(valid)).toEqual({});
  });

  it("explains each problem", () => {
    const errors = validateMentorForm({
      name: "Has Space",
      persona: "x".repeat(1001),
      images: [],
      voice: null,
      consent: false,
    });
    expect(Object.keys(errors).sort()).toEqual(["consent", "images", "name", "persona"]);
  });

  it("limits the number and size of files", () => {
    const big = { size: 16 * 1024 * 1024, name: "big.png" };
    expect(validateMentorForm({ ...valid, images: Array(9).fill(image) }).images).toBeTruthy();
    expect(validateMentorForm({ ...valid, images: [big] }).images).toBeTruthy();
    expect(validateMentorForm({ ...valid, voice: { size: 31 * 1024 * 1024 } }).voice).toBeTruthy();
  });
});

describe("createMentor", () => {
  it("sends the files as a form and returns the new mentor", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ name: "asha", state: "preparing" }, 201));
    vi.stubGlobal("fetch", fetchMock);
    const image = new File(["img"], "front.heic");
    const voice = new File(["snd"], "me.webm");

    const result = await createMentor({
      name: "asha",
      persona: "Kind.",
      expressiveness: 0.7,
      images: [image],
      voice,
      consent: true,
    });

    expect(result).toEqual({ name: "asha", state: "preparing" });
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${AI_SERVICE_URL}/mentors`);
    expect(options.method).toBe("POST");
    const form = options.body;
    expect(form.get("name")).toBe("asha");
    expect(form.get("expressiveness")).toBe("0.7");
    expect(form.get("consent")).toBe("true");
    expect(form.getAll("images")).toHaveLength(1);
    expect(form.get("voice").name).toBe("me.webm");
  });

  it("surfaces the server's explanation when the upload is rejected", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse({ detail: "A mentor with this name already exists." }, 409),
    );
    await expect(
      createMentor({
        name: "a",
        persona: "",
        expressiveness: 0.5,
        images: [],
        voice: null,
        consent: true,
      }),
    ).rejects.toThrow("already exists");
  });
});

describe("editing a mentor", () => {
  const image = new File(["x"], "a.png");

  it("does not ask for photos or permission when nothing new is uploaded", () => {
    const form = { name: "asha", persona: "", images: [], voice: null, consent: false };
    expect(validateMentorForm(form, { existingImages: 2 })).toEqual({});
    expect(validateMentorForm(form, { existingImages: 0 }).images).toBeTruthy();
    expect(
      validateMentorForm({ ...form, images: [image] }, { existingImages: 2 }).consent,
    ).toBeTruthy();
    expect(
      validateMentorForm(
        { ...form, images: Array(7).fill(image), consent: true },
        { existingImages: 2 },
      ).images,
    ).toBeTruthy();
  });

  it("formats speed and pitch for the service and reads them back", () => {
    expect([formatRate(10), formatRate(-5), formatRate(0)]).toEqual(["+10%", "-5%", "+0%"]);
    expect([formatPitch(3), formatPitch(-12)]).toEqual(["+3Hz", "-12Hz"]);
    expect([parseAdjustment("+15%"), parseAdjustment("-3Hz"), parseAdjustment("")]).toEqual([
      15, -3, 0,
    ]);
  });

  it("sends only the changes with a PATCH request", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ name: "asha", state: "preparing" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateMentor("asha", {
      persona: "Brisk.",
      expressiveness: 0.3,
      voiceId: "en-GB-RyanNeural",
      rate: 10,
      pitch: -4,
      images: [image],
      voice: null,
      consent: true,
      removeImages: ["image_1.png", "image_3.webp"],
      removeVoice: true,
    });

    expect(result.state).toBe("preparing");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${AI_SERVICE_URL}/mentors/asha`);
    expect(options.method).toBe("PATCH");
    const form = options.body;
    expect(form.get("name")).toBeNull();
    expect(form.get("persona")).toBe("Brisk.");
    expect(form.get("voice_id")).toBe("en-GB-RyanNeural");
    expect(form.get("speech_rate")).toBe("+10%");
    expect(form.get("speech_pitch")).toBe("-4Hz");
    expect(form.getAll("remove_images")).toEqual(["image_1.png", "image_3.webp"]);
    expect(form.get("remove_voice")).toBe("true");
    expect(form.getAll("images")).toHaveLength(1);
  });

  it("clears the chosen voice when the default is picked", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ name: "asha", state: "ready" }));
    vi.stubGlobal("fetch", fetchMock);

    await updateMentor("asha", { persona: "", expressiveness: 0.5, voiceId: "", images: [] });

    const form = fetchMock.mock.calls[0][1].body;
    expect(form.get("voice_id")).toBe("");
    expect(form.get("speech_rate")).toBeNull();
  });
});
