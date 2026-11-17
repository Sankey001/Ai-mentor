import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Brain,
  ChevronLeft,
  ChevronRight,
  GraduationCap,
  ListChecks,
  Mic,
  Send,
  SlidersHorizontal,
  Square,
  UserPlus,
} from "lucide-react";
import LessonPicker from "../components/learning/LessonPicker";
import LessonNotes from "../components/mentor/LessonNotes";
import MentorStage from "../components/mentor/MentorStage";
import {
  flattenLessons,
  formatSize,
  formatTimeLeft,
  getPack,
  lessonInfo,
  listMentors,
  listModels,
  loadCourseContent,
  packStatus,
  preparePacks,
  stopPreparing,
  streamTalk,
  workerStatus,
} from "../service/mentorLive";
import "../components/learning/learning-workspace.css";
import "../components/mentor/mentor.css";

const QUICK_PROMPTS = ["Give me an everyday example", "Explain it more simply", "Quiz me on this"];
const MENTOR_KEY = "ai-mentor-live-mentor";
const MODEL_KEY = "ai-mentor-live-model";
const THINK_KEY = "ai-mentor-live-think";
const PACK_POLL_MS = 3000;
const LESSONS_AHEAD = 20; // kept prepared after the open lesson
const LESSONS_PER_STEP = 5; // asked for at a time, so the nearest ones are ready first
const PACK_LABEL = { ready: "Ready to play", preparing: "Being prepared", failed: "Not prepared" };
const describe = (values) => [...values].filter(Boolean).join(" / ") || "none";
const modelKey = (option) => `${option.provider}|${option.model}`;
const savedModel = () => {
  try {
    return JSON.parse(localStorage.getItem(MODEL_KEY));
  } catch {
    return null;
  }
};

export default function MentorClassroom() {
  const { courseId = "1" } = useParams();
  const stageRef = useRef(null);
  const logRef = useRef(null);
  const historyRef = useRef([]);
  const abortRef = useRef(null);
  const recognizerRef = useRef(null);
  // The prepared lesson being played: { sentences, next, paused }. `next` is the
  // sentence to go on with after a question.
  const lessonRef = useRef(null);

  const [course, setCourse] = useState(null);
  const [courseError, setCourseError] = useState("");
  const [currentLesson, setCurrentLesson] = useState(null);
  const [mentors, setMentors] = useState([]);
  const [mentor, setMentor] = useState("");
  const [serviceDown, setServiceDown] = useState(false);
  const [workers, setWorkers] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);
  const [modelChoices, setModelChoices] = useState(null);
  const [modelPick, setModelPick] = useState(savedModel);
  const [packs, setPacks] = useState(null);
  // The prepared lesson sentence being spoken: { message, step }.
  const [spoken, setSpoken] = useState(null);

  // The AI that writes the mentor's replies: the learner's pick when it is still
  // usable, else the service's default, else any usable model.
  const modelOptions = modelChoices?.options || [];
  const usableModels = modelOptions.filter((option) => option.available);
  const findUsable = (choice) =>
    choice &&
    usableModels.find(
      (option) => option.provider === choice.provider && option.model === choice.model,
    );
  const activeModel =
    findUsable(modelPick) || findUsable(modelChoices?.default) || usableModels[0] || null;
  const modelSource = activeModel?.source || "local";
  // "Think first" is remembered, but only applies to a model that can think.
  const [thinkOn, setThinkOn] = useState(() => localStorage.getItem(THINK_KEY) === "true");
  const canThink = Boolean(activeModel?.thinking);
  const thinking = thinkOn && canThink;
  const toggleThink = () => {
    const next = !thinkOn;
    setThinkOn(next);
    localStorage.setItem(THINK_KEY, String(next));
  };

  const lessons = useMemo(() => flattenLessons(course?.modules), [course]);
  const lessonIndex = lessons.findIndex((lesson) => lesson.id === currentLesson?.id);
  const moduleTitle = lessons[lessonIndex]?.moduleTitle || "";
  const readyMentors = mentors.filter((item) => item.state === "ready");
  const courseLessons = useMemo(
    () => lessons.map((lesson) => lessonInfo(course, lesson)),
    [course, lessons],
  );
  const packState = packs?.lessons?.[lessonIndex]?.state;

  // Course chapters
  useEffect(() => {
    const controller = new AbortController();
    setCourse(null);
    setCourseError("");
    loadCourseContent(courseId, { token: localStorage.getItem("token"), signal: controller.signal })
      .then((loaded) => {
        setCourse(loaded);
        setCurrentLesson(flattenLessons(loaded.modules)[0] || null);
      })
      .catch((error) => {
        if (error.name !== "AbortError") setCourseError(error.message);
      });
    return () => controller.abort();
  }, [courseId]);

  // Mentors and worker status
  const refreshService = useCallback(async () => {
    // The model list is optional: without it, replies use the service's default.
    listModels()
      .then((choices) => {
        if (Array.isArray(choices?.options)) setModelChoices(choices);
      })
      .catch(() => {});
    try {
      const [list, status] = await Promise.all([listMentors(), workerStatus()]);
      setMentors(list);
      setWorkers(status);
      setServiceDown(false);
      setMentor((current) => {
        const ready = list.filter((item) => item.state === "ready").map((item) => item.name);
        if (ready.includes(current)) return current;
        const saved = localStorage.getItem(MENTOR_KEY);
        return ready.includes(saved) ? saved : ready[0] || "";
      });
    } catch {
      setServiceDown(true);
    }
  }, []);

  useEffect(() => {
    refreshService();
    const timer = setInterval(refreshService, 15000);
    return () => clearInterval(timer);
  }, [refreshService]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      recognizerRef.current?.abort?.();
    },
    [],
  );

  useEffect(() => {
    logRef.current?.scrollTo?.({ top: logRef.current.scrollHeight });
  }, [messages]);

  // Prepared lessons: how many of this course's are ready for this mentor.
  const refreshPacks = useCallback(async () => {
    if (!mentor || !courseLessons.length) return setPacks(null);
    try {
      setPacks({ ...(await packStatus(mentor, courseLessons)), mentor });
    } catch {
      // Without prepared lessons the mentor teaches live.
    }
  }, [mentor, courseLessons]);

  useEffect(() => {
    refreshPacks();
  }, [refreshPacks]);

  const preparing = (packs?.preparing || 0) > 0;
  useEffect(() => {
    if (!preparing) return undefined;
    const timer = setInterval(refreshPacks, PACK_POLL_MS);
    return () => clearInterval(timer);
  }, [preparing, refreshPacks]);

  // The open lesson and the LESSONS_AHEAD after it are kept prepared in the
  // background: on opening a lesson, it goes first, then the next few unprepared
  // lessons after it; each time those are done, the next few, until all are ready.
  const queuedForRef = useRef("");
  const aheadStates = packs?.mentor === mentor ? packs.lessons : null;
  const ahead = useMemo(() => {
    if (!aheadStates || aheadStates.length !== courseLessons.length || lessonIndex < 0) return null;
    const end = Math.min(courseLessons.length, lessonIndex + 1 + LESSONS_AHEAD);
    const missing = [];
    for (let index = lessonIndex + 1; index < end; index += 1) {
      if (aheadStates[index]?.state === "missing") missing.push(index);
    }
    return {
      current: aheadStates[lessonIndex]?.state === "missing",
      missing,
      ready: aheadStates.slice(lessonIndex + 1, end).filter((item) => item.state === "ready")
        .length,
      size: end - lessonIndex - 1,
    };
  }, [aheadStates, courseLessons.length, lessonIndex]);

  useEffect(() => {
    if (!mentor || !ahead) return;
    const place = `${mentor}|${lessonIndex}`;
    const moved = queuedForRef.current !== place;
    // On the same lesson, the next few are asked for only once the last few are done.
    if (!moved && packs.preparing > 0) return;
    queuedForRef.current = place;
    const next = ahead.missing.slice(0, LESSONS_PER_STEP).map((index) => courseLessons[index]);
    if (!ahead.current && !next.length) return;
    (ahead.current
      ? preparePacks(mentor, [courseLessons[lessonIndex]], { urgent: true })
      : Promise.resolve()
    )
      .then(() => next.length && preparePacks(mentor, next))
      .then(refreshPacks)
      .catch(() => {});
  }, [mentor, lessonIndex, ahead, packs?.preparing, courseLessons, refreshPacks]);

  // A lesson that is playing belongs to its chapter: leaving the chapter ends it.
  useEffect(() => {
    if (!lessonRef.current) return;
    lessonRef.current = null;
    stageRef.current?.stop();
  }, [currentLesson?.id]);

  const prepareCourse = () =>
    preparePacks(mentor, courseLessons)
      .then((status) => setPacks({ ...status, mentor }))
      .catch(() => {});
  const stopPreparingCourse = () =>
    stopPreparing(mentor)
      .then(refreshPacks)
      .catch(() => {});

  const chooseMentor = (name) => {
    abortRef.current?.abort();
    lessonRef.current = null;
    stageRef.current?.stop();
    historyRef.current = [];
    setMessages([]);
    setMentor(name);
    localStorage.setItem(MENTOR_KEY, name);
  };

  const chooseModel = (option) => {
    if (!option) return;
    const pick = { provider: option.provider, model: option.model };
    setModelPick(pick);
    localStorage.setItem(MODEL_KEY, JSON.stringify(pick));
  };

  // Switching between this computer and an online API picks that side's first usable model.
  const chooseSource = (source) =>
    chooseModel(usableModels.find((option) => option.source === source));

  const send = async ({ message = "", teach = false }) => {
    const text = message.trim();
    if (busy || !mentor || (!text && !teach)) return;
    const lesson = lessonIndex >= 0 ? courseLessons[lessonIndex] : undefined;
    // A question pauses the lesson being played; it goes on once it is answered.
    if (lessonRef.current) {
      lessonRef.current.paused =
        !teach && lessonRef.current.next < lessonRef.current.sentences.length;
      if (!lessonRef.current.paused) lessonRef.current = null;
    }
    const shown = text || `Teach me: ${currentLesson?.title}`;
    const id = Date.now();
    setMessages((list) => [
      ...list,
      { id: `${id}-q`, role: "learner", text: shown },
      { id: `${id}-a`, role: "mentor", text: "", pending: true, thinking },
    ]);
    setBusy(true);
    setDraft("");
    stageRef.current?.stop();
    const controller = new AbortController();
    abortRef.current = controller;
    const used = { voice: new Set(), face: new Set() };
    const sentWith = activeModel;
    const sentThinking = thinking;
    let reply = "";
    let answeredBy = "";
    const update = (patch) =>
      setMessages((list) =>
        list.map((item) => (item.id === `${id}-a` ? { ...item, ...patch } : item)),
      );

    try {
      await streamTalk({
        mentor,
        message: text,
        lesson,
        history: historyRef.current.slice(-20),
        provider: sentWith?.provider,
        model: sentWith?.model,
        think: sentThinking,
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "text") {
            // The reply so far, while the mentor is still writing it.
            update({ text: event.text, pending: false });
          } else if (event.type === "reply") {
            reply = event.text;
            answeredBy =
              event.provider === "ollama" && sentWith?.provider === "ollama"
                ? `${sentWith.model}${sentThinking ? " (thinking)" : ""}`
                : event.provider;
            update({ text: reply, pending: false, provider: event.provider });
          } else if (event.type === "chunk") {
            used.voice.add(event.voice);
            used.face.add(event.face);
            stageRef.current?.enqueue(event);
          }
        },
      });
      update({
        meta: `AI: ${answeredBy || "none"} · Voice: ${describe(used.voice)} · Face: ${describe(used.face)}`,
      });
      historyRef.current.push({ role: "learner", text: shown }, { role: "mentor", text: reply });
    } catch (error) {
      if (error.name !== "AbortError") {
        update({
          pending: false,
          failed: true,
          text: `The mentor could not answer. ${error.message || ""}`.trim(),
        });
      }
    } finally {
      setBusy(false);
      refreshService();
    }
  };

  const playLesson = (from) => {
    lessonRef.current.paused = false;
    lessonRef.current.sentences
      .slice(from)
      .forEach((sentence, offset) =>
        stageRef.current?.enqueue({ ...sentence, lessonStep: from + offset }),
      );
  };

  // "Teach this lesson": a prepared lesson starts at once and needs no GPU; without
  // one the mentor teaches live, as for any question.
  const teach = async () => {
    if (busy || !mentor || lessonIndex < 0) return;
    let pack = null;
    try {
      pack = await getPack(mentor, courseLessons[lessonIndex]);
    } catch {
      // Not reachable or not prepared: teach live.
    }
    if (!pack?.sentences?.length) return send({ teach: true });
    stageRef.current?.stop();
    const id = Date.now();
    const shown = `Teach me: ${currentLesson.title}`;
    const text = pack.sentences.map((sentence) => sentence.text).join(" ");
    const voices = new Set(pack.sentences.map((sentence) => sentence.voice));
    const faces = new Set(pack.sentences.map((sentence) => sentence.face));
    setMessages((list) => [
      ...list,
      { id: `${id}-q`, role: "learner", text: shown },
      {
        id: `${id}-a`,
        role: "mentor",
        text,
        lesson: { title: currentLesson.title, sentences: pack.sentences },
        meta: `Prepared lesson · Voice: ${describe(voices)} · Face: ${describe(faces)}`,
      },
    ]);
    // The service adds the full script to questions itself; the history only notes it.
    historyRef.current.push(
      { role: "learner", text: shown },
      { role: "mentor", text: text.slice(0, 600) },
    );
    lessonRef.current = { sentences: pack.sentences, next: 0, paused: false, message: `${id}-a` };
    playLesson(0);
    return undefined;
  };

  // After a question is answered and spoken, the lesson goes on with its next sentence.
  useEffect(() => {
    if (!busy && !speaking && lessonRef.current?.paused) playLesson(lessonRef.current.next);
  }, [busy, speaking]);

  const stopAll = () => {
    abortRef.current?.abort();
    lessonRef.current = null;
    stageRef.current?.stop();
    setBusy(false);
    setMessages((list) => list.filter((item) => !item.pending));
  };

  const Recognition =
    typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);
  const listen = () => {
    if (!Recognition || listening) return;
    const recognizer = new Recognition();
    recognizerRef.current = recognizer;
    recognizer.lang = navigator.language || "en-US";
    recognizer.onresult = (event) => send({ message: event.results[0][0].transcript });
    recognizer.onend = () => setListening(false);
    recognizer.onerror = () => setListening(false);
    setListening(true);
    recognizer.start();
  };

  const goTo = (offset) => {
    const next = lessons[lessonIndex + offset];
    if (next) setCurrentLesson(next);
  };

  const canTalk = Boolean(mentor) && !serviceDown;
  const ownVoice =
    workers?.voice && mentors.find((item) => item.name === mentor)?.voice === "cloned";

  return (
    <div className="lw-page">
      <div className="lw-inner">
        <nav className="lw-breadcrumb" aria-label="Breadcrumb">
          <span>{course?.title || "Course"}</span>
          <ChevronRight aria-hidden="true" />
          <span>{moduleTitle || "Live mentor"}</span>
        </nav>
        <header className="lw-heading">
          <span className="lw-eyebrow">
            LESSON {Math.max(0, lessonIndex + 1)} / {lessons.length} · LIVE MENTOR
          </span>
          <h1>{currentLesson?.title || courseError || "Loading the course…"}</h1>
        </header>

        {serviceDown && (
          <p className="mt-notice" role="alert">
            The mentor service is not reachable. Start it with <code>start_app.py</code>, then this
            page reconnects on its own.
          </p>
        )}
        {!serviceDown && mentors.length > 0 && readyMentors.length === 0 && (
          <p className="mt-notice" role="status">
            Your mentor is still being prepared. It appears here as soon as it is ready.
          </p>
        )}

        <div className="lw-toolbar">
          <LessonPicker
            modules={course?.modules || []}
            currentLesson={currentLesson}
            onSelect={setCurrentLesson}
          />
          <label className="mt-status">
            <span className="sr-only">Mentor</span>
            <select
              className="mt-select"
              value={mentor}
              onChange={(event) => chooseMentor(event.target.value)}
              disabled={!readyMentors.length}
              aria-label="Mentor"
            >
              {!readyMentors.length && <option value="">No mentor yet</option>}
              {readyMentors.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="mt-status">
            <span className="sr-only">Run on</span>
            <select
              className="mt-select"
              value={modelSource}
              onChange={(event) => chooseSource(event.target.value)}
              disabled={!usableModels.length || busy}
              aria-label="Run on"
              title="Where the mentor's replies are written"
            >
              <option
                value="local"
                disabled={!usableModels.some((option) => option.source === "local")}
              >
                This computer
              </option>
              <option
                value="api"
                disabled={!usableModels.some((option) => option.source === "api")}
              >
                Online API
              </option>
            </select>
          </label>
          <label className="mt-status">
            <span className="sr-only">AI model</span>
            <select
              className="mt-select"
              value={activeModel ? modelKey(activeModel) : ""}
              onChange={(event) =>
                chooseModel(modelOptions.find((option) => modelKey(option) === event.target.value))
              }
              disabled={!usableModels.length || busy}
              aria-label="AI model"
            >
              {!activeModel && <option value="">Default model</option>}
              {modelOptions
                .filter((option) => option.source === modelSource)
                .map((option) => (
                  <option
                    key={modelKey(option)}
                    value={modelKey(option)}
                    disabled={!option.available}
                  >
                    {option.available ? option.label : `${option.label} (no API key)`}
                  </option>
                ))}
            </select>
          </label>
          <div className="mt-status" aria-live="polite">
            <span>
              <span className="mt-dot" data-state={ownVoice ? "ok" : "idle"} />
              {ownVoice ? "Mentor's own voice" : "Standard voice"}
            </span>
            <span>
              <span className="mt-dot" data-state={workers?.face ? "ok" : "off"} />
              {workers?.face ? "Lip-sync on" : "Lip-sync off"}
            </span>
          </div>
          <div className="lw-toolbar-actions">
            {preparing ? (
              <button
                type="button"
                className="lw-button"
                onClick={stopPreparingCourse}
                title="Stop preparing lessons; what is finished is kept"
              >
                <Square size={15} aria-hidden="true" />
                <span>Stop preparing</span>
              </button>
            ) : (
              <button
                type="button"
                className="lw-button"
                onClick={prepareCourse}
                disabled={!canTalk || !packs || packs.ready === packs.total}
                title="Write, voice and lip-sync every lesson now, so each one plays at once later"
              >
                <ListChecks size={17} aria-hidden="true" />
                <span>Prepare whole course</span>
              </button>
            )}
            <Link className="lw-button" to="/mentor-studio">
              <UserPlus size={17} aria-hidden="true" />
              <span>Create a mentor</span>
            </Link>
            <Link className="lw-button" to="/mentor-settings" aria-label="AI settings">
              <SlidersHorizontal size={17} aria-hidden="true" />
            </Link>
          </div>
        </div>

        {packs && (
          <section className="mt-prepare" role="status" aria-label="Prepared lessons">
            <progress
              className="mt-progress"
              value={packs.ready}
              max={packs.total}
              aria-label="Lessons prepared"
            />
            <dl className="mt-prepare-list">
              <div>
                <dt>Prepared</dt>
                <dd>
                  {packs.ready} of {packs.total} lessons
                </dd>
              </div>
              {ahead && ahead.size > 0 && (
                <div>
                  <dt>Next {ahead.size}</dt>
                  <dd>
                    {ahead.ready} of {ahead.size} ready
                  </dd>
                </div>
              )}
              {preparing && (
                <div>
                  <dt>To go</dt>
                  <dd>
                    {packs.preparing} {packs.preparing === 1 ? "lesson" : "lessons"},{" "}
                    {formatTimeLeft(packs.seconds_left)} left
                  </dd>
                </div>
              )}
              {preparing && packs.doing && (
                <div>
                  <dt>Now</dt>
                  <dd className="mt-prepare-doing">{packs.doing}</dd>
                </div>
              )}
              {packs.bytes > 0 && (
                <div>
                  <dt>On disk</dt>
                  <dd>{formatSize(packs.bytes)}</dd>
                </div>
              )}
            </dl>
          </section>
        )}

        <div className="mt-workspace">
          <div className="lw-media-column">
            <div className="lw-video-card">
              <MentorStage
                ref={stageRef}
                mentor={canTalk ? mentor : ""}
                thinking={busy && !speaking}
                onSpeakingChange={setSpeaking}
                onChunkStart={(chunk) => {
                  if (lessonRef.current && chunk.lessonStep !== undefined) {
                    lessonRef.current.next = chunk.lessonStep + 1;
                    setSpoken({ message: lessonRef.current.message, step: chunk.lessonStep });
                  } else {
                    setSpoken(null);
                  }
                }}
              />
              <div className="lw-media-footer">
                <button
                  type="button"
                  className="lw-button lw-button--quiet"
                  onClick={() => goTo(-1)}
                  disabled={lessonIndex <= 0}
                  aria-label="Previous lesson"
                >
                  <ChevronLeft size={16} aria-hidden="true" />
                  <span className="mt-wide-only">Previous</span>
                </button>
                <button
                  type="button"
                  className="lw-button lw-button--primary"
                  onClick={teach}
                  disabled={!canTalk || busy || !currentLesson}
                  title={
                    packState === "ready"
                      ? "Prepared: starts at once"
                      : "Not prepared yet: the mentor teaches it live"
                  }
                >
                  <GraduationCap size={17} aria-hidden="true" />
                  <span>Teach this lesson</span>
                  {PACK_LABEL[packState] && (
                    <span className="mt-badge" data-state={packState}>
                      {PACK_LABEL[packState]}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className="lw-button lw-button--quiet"
                  onClick={() => goTo(1)}
                  disabled={lessonIndex < 0 || lessonIndex >= lessons.length - 1}
                  aria-label="Next lesson"
                >
                  <span className="mt-wide-only">Next</span>
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              </div>
            </div>
          </div>

          <section className="mt-panel" aria-label="Conversation with the mentor">
            <div className="mt-log" ref={logRef} aria-live="polite">
              {!messages.length && (
                <p className="lw-empty-copy">
                  Press “Teach this lesson”, or ask the mentor a question about it.
                </p>
              )}
              {messages.map((item) => (
                <div
                  key={item.id}
                  className="mt-msg"
                  data-role={item.role}
                  data-lesson={item.lesson ? "" : undefined}
                >
                  {item.pending ? (
                    item.thinking ? (
                      "Thinking it through… this can take a few minutes."
                    ) : (
                      "Thinking…"
                    )
                  ) : item.lesson ? (
                    <LessonNotes
                      title={item.lesson.title}
                      sentences={item.lesson.sentences}
                      current={speaking && spoken?.message === item.id ? spoken.step : -1}
                    />
                  ) : (
                    item.text
                  )}
                  {item.meta && <div className="mt-msg-meta">{item.meta}</div>}
                </div>
              ))}
            </div>
            <div className="mt-chips">
              <button
                type="button"
                className="mt-chip mt-chip--toggle"
                aria-pressed={thinking}
                disabled={!canThink || busy}
                onClick={toggleThink}
                title={
                  canThink
                    ? "Let the model reason before it answers: slower, often better"
                    : "The selected model cannot think step by step (try qwen3.5)"
                }
              >
                <Brain size={14} aria-hidden="true" />
                <span>Think first</span>
              </button>
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="mt-chip"
                  disabled={!canTalk || busy}
                  onClick={() => send({ message: prompt })}
                >
                  {prompt}
                </button>
              ))}
            </div>
            <form
              className="mt-compose"
              onSubmit={(event) => {
                event.preventDefault();
                send({ message: draft });
              }}
            >
              <input
                className="mt-input"
                value={draft}
                maxLength={500}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={canTalk ? "Ask about this lesson" : "Waiting for a mentor"}
                aria-label="Your question"
                disabled={!canTalk}
              />
              {Recognition && (
                <button
                  type="button"
                  className={`lw-button ${listening ? "lw-button--selected" : ""}`}
                  onClick={listen}
                  disabled={!canTalk || busy}
                  aria-label={listening ? "Listening" : "Speak your question"}
                >
                  <Mic size={17} aria-hidden="true" />
                </button>
              )}
              {(busy || speaking) && (
                <button type="button" className="lw-button" onClick={stopAll} aria-label="Stop">
                  <Square size={15} aria-hidden="true" />
                  <span>Stop</span>
                </button>
              )}
              {/* A question can be sent while the mentor is speaking: it interrupts. */}
              {!busy && (
                <button
                  type="submit"
                  className="lw-button lw-button--primary"
                  disabled={!canTalk || !draft.trim()}
                >
                  <Send size={16} aria-hidden="true" />
                  <span>Send</span>
                </button>
              )}
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}
