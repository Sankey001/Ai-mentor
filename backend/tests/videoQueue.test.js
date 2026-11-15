import { test, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import jwt from "jsonwebtoken";

// Env must be set before the app modules are imported.
process.env.NODE_ENV = "test";
process.env.JWT_SECRET = "test-secret-video-queue";
process.env.AI_SERVICE_URL = "http://ai-service.test";

const { default: express } = await import("express");
const { default: AIVideo } = await import("../models/AIVideo.js");
const { default: User } = await import("../models/User.js");
const { default: Course } = await import("../models/Course.js");
const { default: Module } = await import("../models/Module.js");
const { default: Lesson } = await import("../models/Lesson.js");
const { default: Preferences } = await import("../models/Preference.js");
const { processVideoJob, setVideoQueue, closeVideoQueue } = await import("../queues/videoQueue.js");
const { default: aiRoutes } = await import("../routes/aiRoutes.js");

const originals = {
  update: AIVideo.update,
  findOne: AIVideo.findOne,
  create: AIVideo.create,
  findByPkUser: User.findByPk,
  findByPkCourse: Course.findByPk,
  findByPkLesson: Lesson.findByPk,
  findOneModule: Module.findOne,
  findOnePreferences: Preferences.findOne,
  fetch: globalThis.fetch,
};

const aiServiceBodies = [];
let server;
let baseUrl;

before(async () => {
  AIVideo.update = async () => [1];
  AIVideo.findOne = async () => null;
  AIVideo.create = async () => ({ id: "video-db-id" });
  User.findByPk = async (id) => ({ id, role: "student", purchasedCourses: [{ courseId: 1 }] });
  Course.findByPk = async (id) => ({ id: Number(id), title: `Course ${id}` });
  Lesson.findByPk = async (id) => ({ id: Number(id), moduleId: 1, title: `Lesson ${id}` });
  Module.findOne = async () => ({ id: 1, courseId: 1 });
  Preferences.findOne = async () => null;

  // The AI service is the only outer boundary that is stubbed.
  globalThis.fetch = async (url, options) => {
    if (String(url) === `${process.env.AI_SERVICE_URL}/generate`) {
      aiServiceBodies.push(JSON.parse(options.body));
      return Response.json({ jobId: "python-job", text_file: "python-job.txt" });
    }
    return originals.fetch(url, options);
  };

  // Run the real worker function inline in place of Redis.
  setVideoQueue({
    add: async (_name, data) => {
      await processVideoJob({ data });
      return { id: "inline-job" };
    },
  });

  const app = express();
  app.use(express.json());
  app.use("/api/ai", aiRoutes);
  server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  AIVideo.update = originals.update;
  AIVideo.findOne = originals.findOne;
  AIVideo.create = originals.create;
  User.findByPk = originals.findByPkUser;
  Course.findByPk = originals.findByPkCourse;
  Lesson.findByPk = originals.findByPkLesson;
  Module.findOne = originals.findOneModule;
  Preferences.findOne = originals.findOnePreferences;
  globalThis.fetch = originals.fetch;
  await closeVideoQueue();
  await new Promise((resolve) => server.close(resolve));
});

beforeEach(() => {
  aiServiceBodies.length = 0;
});

const baseJob = {
  aiVideoId: "video-db-id",
  courseTitle: "Python",
  lessonTitle: "Loops",
  celebrity: "owl",
  userPreferences: null,
};

test("worker forwards the voice settings to the AI service", async () => {
  await processVideoJob({
    data: {
      ...baseJob,
      voice_id: "en-GB-RyanNeural",
      speech_rate: "+10%",
      speech_pitch: "-5Hz",
    },
  });

  assert.equal(aiServiceBodies.length, 1);
  assert.deepEqual(aiServiceBodies[0], {
    course: "Python",
    topic: "Loops",
    celebrity: "owl",
    preferences: null,
    voice_id: "en-GB-RyanNeural",
    speech_rate: "+10%",
    speech_pitch: "-5Hz",
  });
});

test("worker omits voice settings that were not chosen", async () => {
  await processVideoJob({ data: { ...baseJob, voice_id: "", speech_rate: undefined } });

  assert.deepEqual(aiServiceBodies[0], {
    course: "Python",
    topic: "Loops",
    celebrity: "owl",
    preferences: null,
  });
});

test("generate-video request carries the chosen voice through to the AI service", async () => {
  const res = await originals.fetch(`${baseUrl}/api/ai/generate-video`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${jwt.sign({ id: 1 }, process.env.JWT_SECRET)}`,
    },
    body: JSON.stringify({
      courseId: 1,
      lessonId: 1,
      celebrity: "owl",
      voice_id: "en-IN-NeerjaNeural",
      speech_rate: "+5%",
      speech_pitch: "+2Hz",
    }),
  });

  assert.equal(res.status, 200);
  assert.equal(aiServiceBodies.length, 1);
  assert.equal(aiServiceBodies[0].voice_id, "en-IN-NeerjaNeural");
  assert.equal(aiServiceBodies[0].speech_rate, "+5%");
  assert.equal(aiServiceBodies[0].speech_pitch, "+2Hz");
  assert.equal(aiServiceBodies[0].topic, "Lesson 1");
});
