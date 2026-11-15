import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ChevronLeft,
  ChevronRight,
  GraduationCap,
  Mic,
  Send,
  Square,
  UserPlus,
} from "lucide-react";
import LessonPicker from "../components/learning/LessonPicker";
import MentorStage from "../components/mentor/MentorStage";
import {
  flattenLessons,
  listMentors,
  loadCourseContent,
  streamTalk,
  workerStatus,
} from "../service/mentorLive";
import "../components/learning/learning-workspace.css";
import "../components/mentor/mentor.css";

const QUICK_PROMPTS = ["Give me an everyday example", "Explain it more simply", "Quiz me on this"];
const MENTOR_KEY = "ai-mentor-live-mentor";
const describe = (values) => [...values].filter(Boolean).join(" / ") || "none";

export default function MentorClassroom() {
  const { courseId = "1" } = useParams();
  const stageRef = useRef(null);
  const logRef = useRef(null);
  const historyRef = useRef([]);
  const abortRef = useRef(null);
  const recognizerRef = useRef(null);

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

  const lessons = useMemo(() => flattenLessons(course?.modules), [course]);
  const lessonIndex = lessons.findIndex((lesson) => lesson.id === currentLesson?.id);
  const moduleTitle = lessons[lessonIndex]?.moduleTitle || "";
  const readyMentors = mentors.filter((item) => item.state === "ready");

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

  const chooseMentor = (name) => {
    abortRef.current?.abort();
    stageRef.current?.stop();
    historyRef.current = [];
    setMessages([]);
    setMentor(name);
    localStorage.setItem(MENTOR_KEY, name);
  };

  const send = async ({ message = "", teach = false }) => {
    const text = message.trim();
    if (busy || !mentor || (!text && !teach)) return;
    const lesson = currentLesson
      ? { course: course?.title || "", module: moduleTitle, title: currentLesson.title }
      : undefined;
    const shown = text || `Teach me: ${currentLesson?.title}`;
    const id = Date.now();
    setMessages((list) => [
      ...list,
      { id: `${id}-q`, role: "learner", text: shown },
      { id: `${id}-a`, role: "mentor", text: "", pending: true },
    ]);
    setBusy(true);
    setDraft("");
    stageRef.current?.stop();
    const controller = new AbortController();
    abortRef.current = controller;
    const used = { voice: new Set(), face: new Set() };
    let reply = "";
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
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "reply") {
            reply = event.text;
            update({ text: reply, pending: false, provider: event.provider });
          } else if (event.type === "chunk") {
            used.voice.add(event.voice);
            used.face.add(event.face);
            stageRef.current?.enqueue(event);
          }
        },
      });
      update({ meta: `Voice: ${describe(used.voice)} · Face: ${describe(used.face)}` });
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

  const stopAll = () => {
    abortRef.current?.abort();
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
            <Link className="lw-button" to="/mentor-studio">
              <UserPlus size={17} aria-hidden="true" />
              <span>Create a mentor</span>
            </Link>
          </div>
        </div>

        <div className="mt-workspace">
          <div className="lw-media-column">
            <div className="lw-video-card">
              <MentorStage
                ref={stageRef}
                mentor={canTalk ? mentor : ""}
                thinking={busy && !speaking}
                onSpeakingChange={setSpeaking}
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
                  onClick={() => send({ teach: true })}
                  disabled={!canTalk || busy || !currentLesson}
                >
                  <GraduationCap size={17} aria-hidden="true" />
                  <span>Teach this lesson</span>
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
                <div key={item.id} className="mt-msg" data-role={item.role}>
                  {item.pending ? "Thinking…" : item.text}
                  {item.meta && <div className="mt-msg-meta">{item.meta}</div>}
                </div>
              ))}
            </div>
            <div className="mt-chips">
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
              {busy || speaking ? (
                <button type="button" className="lw-button" onClick={stopAll} aria-label="Stop">
                  <Square size={15} aria-hidden="true" />
                  <span>Stop</span>
                </button>
              ) : (
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
