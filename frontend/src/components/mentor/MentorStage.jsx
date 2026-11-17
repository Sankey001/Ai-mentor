import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { idleClipUrl, mediaUrl } from "../../service/mentorLive";
import "./mentor.css";

const STATE_LABEL = { idle: "Listening", thinking: "Thinking", speaking: "Speaking" };

// Shows the mentor: a looping idle clip, with each spoken sentence played over it in order.
// `onChunkStart` hears about each sentence as it begins, so a lesson knows where it is.
const MentorStage = forwardRef(function MentorStage(
  { mentor, thinking, onSpeakingChange, onChunkStart },
  ref,
) {
  const talkingRef = useRef(null);
  const audioRef = useRef(null);
  const queueRef = useRef([]);
  const timerRef = useRef(null);
  const playingRef = useRef(false);
  const [caption, setCaption] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [showVideo, setShowVideo] = useState(false);

  const setPlaying = (value) => {
    playingRef.current = value;
    setSpeaking(value);
    onSpeakingChange?.(value);
  };

  const playNext = () => {
    const chunk = queueRef.current.shift();
    clearTimeout(timerRef.current);
    if (!chunk) {
      setPlaying(false);
      setShowVideo(false);
      setCaption("");
      return;
    }
    setPlaying(true);
    onChunkStart?.(chunk);
    setCaption(chunk.text);
    setShowVideo(chunk.kind === "video");
    const player =
      chunk.kind === "video"
        ? talkingRef.current
        : chunk.kind === "audio"
          ? audioRef.current
          : null;
    if (!player) {
      // Text only: leave the caption up long enough to read, then move on.
      timerRef.current = setTimeout(playNext, 350 * chunk.text.split(" ").length + 600);
      return;
    }
    player.src = mediaUrl(chunk.url);
    const started = player.play();
    started?.catch?.(playNext);
  };

  const stop = () => {
    queueRef.current = [];
    clearTimeout(timerRef.current);
    talkingRef.current?.pause();
    audioRef.current?.pause();
    setPlaying(false);
    setShowVideo(false);
    setCaption("");
  };

  useImperativeHandle(ref, () => ({
    enqueue(chunk) {
      queueRef.current.push(chunk);
      if (!playingRef.current) playNext();
    },
    stop,
  }));

  useEffect(() => stop, [mentor]); // eslint-disable-line react-hooks/exhaustive-deps

  const state = speaking ? "speaking" : thinking ? "thinking" : "idle";
  return (
    <div className="mt-stage">
      {mentor ? (
        <>
          <video
            key={mentor}
            src={idleClipUrl(mentor)}
            muted
            loop
            autoPlay
            playsInline
            aria-hidden="true"
          />
          <video
            ref={talkingRef}
            playsInline
            onEnded={playNext}
            onError={playNext}
            style={{ visibility: showVideo ? "visible" : "hidden" }}
            aria-label={`${mentor} speaking`}
          />
          <audio ref={audioRef} onEnded={playNext} onError={playNext} />
          <span className="mt-pill" role="status">
            <span className="mt-dot" data-state={state} />
            {STATE_LABEL[state]}
          </span>
          {caption && <p className="mt-caption">{caption}</p>}
        </>
      ) : (
        <p className="mt-stage-empty">Choose a mentor to begin.</p>
      )}
    </div>
  );
});

export default MentorStage;
