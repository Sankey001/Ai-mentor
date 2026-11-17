# AI Mentor AI Service Backend

The AI service is a FastAPI application used by AI Mentor to generate lesson scripts, narration audio, lesson videos, subtitles, transcripts, syllabi, quizzes, and flashcards.

For normal local development, use the repository-root npm commands. Direct Python startup is reserved for focused debugging.

## Technology stack

| Layer                         | Technology                           |
| ----------------------------- | ------------------------------------ |
| API framework                 | FastAPI and Uvicorn                  |
| Primary AI provider           | Google Gemini through `google-genai` |
| Optional fallback AI provider | Groq                                 |
| Text-to-speech                | Edge TTS through `edge-tts`          |
| Video processing              | FFmpeg                               |
| Optional cloud delivery       | Cloudinary                           |
| Optional avatar provider      | D-ID                                 |
| Testing                       | pytest and pytest-asyncio            |

## Project structure

```text
ai_service/
|-- backend/
|   |-- api.py
|   |-- config.py
|   |-- graceful_server.py
|   |-- avatar_service.py
|   |-- voices.py
|   |-- subtitles.py
|   |-- job_shutdown.py
|   |-- requirements.txt
|   |-- .env.example
|   `-- input/
|-- workers/          voice worker, face worker, image step
|-- app/              basic live-conversation page
|-- start_app.py      starts the API, website and workers together
|-- outputs/
|   |-- audio/
|   |-- text/
|   |-- talk/         conversation media, removed after an hour
|   `-- video/
`-- tests/
```

Generated media is temporary when Cloudinary delivery succeeds. The service removes local audio, video, and subtitle files after a successful upload while retaining the generated transcript text used by the application.

## Prerequisites

The local environment requires:

- Python available through `python`
- FFmpeg available on `PATH`
- Redis or Memurai listening on port `6379` for queued video generation
- `ai_service/backend/.env` with a real `GEMINI_API_KEY`

Node.js and npm are required for the repository-root setup and development commands.

## Installation

From the repository root, prepare only the AI service:

```bash
npm run setup:ai
```

The command:

- creates or reuses `ai_service/.venv`;
- installs `ai_service/backend/requirements.txt`;
- validates Python, FFmpeg, AI configuration names, AI imports, and Redis or Memurai availability;
- never prints secret values or overwrites an existing `.env` file.

To set up the complete repository, including all Node and React applications, run:

```bash
npm run setup
```

## Configuration

Create `ai_service/backend/.env` from `ai_service/backend/.env.example`.

| Variable                | Requirement                                     | Behavior when unavailable                                                            |
| ----------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------ |
| `GEMINI_API_KEY`        | Required                                        | Setup and AI startup fail with a clear error                                         |
| `GEMINI_MODEL`          | Optional                                        | Uses the default model configured by the service                                     |
| `GROQ_API_KEY`          | Optional                                        | Groq fallback is disabled                                                            |
| `CLOUDINARY_CLOUD_NAME` | Optional as part of the complete Cloudinary set | Cloud delivery is disabled when the set is incomplete                                |
| `CLOUDINARY_API_KEY`    | Optional as part of the complete Cloudinary set | Cloud delivery is disabled when the set is incomplete                                |
| `CLOUDINARY_API_SECRET` | Optional as part of the complete Cloudinary set | Cloud delivery is disabled when the set is incomplete                                |
| `DID_API_KEY`           | Optional                                        | D-ID avatar rendering is unavailable                                                 |
| `DID_API_URL`           | Optional                                        | Uses the service default when omitted                                                |
| `DID_SOURCE_URL`        | Optional                                        | D-ID rendering falls back to the local FFmpeg renderer                               |
| `AI_SERVICE_LOG_LEVEL`  | Optional                                        | Uses `INFO` by default; supports `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL` |
| `LLM_TIMEOUT_SECONDS`   | Optional                                        | Sets the LLM call timeout in seconds; defaults to `60` when omitted                  |
| `VOICE_WORKER_PYTHON`   | Optional                                        | The voice worker is not started; mentors use stock voices                            |
| `FACE_WORKER_PYTHON`    | Optional                                        | The face worker is not started; mentors talk over their idle clip                    |
| `MUSETALK_DIR`          | Optional                                        | Needed together with `FACE_WORKER_PYTHON`                                            |
| `PORTRAIT_PYTHON`       | Optional                                        | Mentor images are not turned into clips                                              |
| `LIVEPORTRAIT_DIR`      | Optional                                        | Needed together with `PORTRAIT_PYTHON`                                               |
| `VOICE_WORKER_URL`      | Optional                                        | Defaults to `http://127.0.0.1:8101`; set it empty to disable the voice worker        |
| `FACE_WORKER_URL`       | Optional                                        | Defaults to `http://127.0.0.1:8102`; set it empty to disable the face worker         |

Setup output reports only variable names and `<configured>` markers. Do not commit `.env` files or share credential values.

### Celebrity source videos

Place source video files in `ai_service/backend/input/`. File names must match the supported source name in lowercase. The service uses `modi.mp4` as the default local fallback when a requested source video is unavailable.

Only validated local files within the configured input directory are eligible for fallback rendering.

### Mentor packs

A mentor pack is a folder named after the mentor in `ai_service/backend/input/`. Every file in it is optional, but a mentor needs at least one image or clip:

```text
ai_service/backend/input/asha/
|-- front.heic        images of the mentor, in any format
|-- side.webp
|-- talking_1.mp4     your own clips of the mentor, if you have any
|-- voice.wav         a clean recording of the mentor's own voice
`-- mentor.json       the mentor's personality
```

A folder with a lowercase name made of letters, digits, `-`, or `_` is accepted as a mentor without a code change. Media files in a pack are ignored by Git. Packs can also be created from the website's mentor studio page, which calls `POST /mentors`.

**Images.** Each image becomes a short clip named `auto_<image>_<hash>.mp4` in which the head moves slightly and the eyes blink. The format is read from the file's content, so the file name and extension do not matter. A clip is made once, remade when its image changes, and removed when its image is deleted. Clear, front-facing photos with the mouth closed work best.

**Clips.** All `.mp4` files in the pack, generated or your own, form the mentor's clip library. All clips of one mentor must share the same resolution. In a conversation each sentence uses one clip, so the mentor changes pose between sentences.

**Voice.** With `voice.wav` (or `.mp3`, `.m4a`, `.flac`) in the pack and the voice worker running, the mentor speaks in that voice and accent. Use 10 to 30 seconds of clear speech with no music. Only use a recording of a person who has agreed to have their voice cloned. Without a sample, or when the worker is down, the mentor's stock voice from `MENTOR_VOICES` in `backend/voices.py` is used.

**Personality.** `mentor.json` holds two optional settings:

```json
{
  "persona": "Warm and a little playful. Short sentences. Calls bugs 'gremlins'.",
  "expressiveness": 0.7
}
```

`persona` is added to the lesson and conversation prompts, so replies are written the way the mentor talks. `expressiveness` runs from `0` (flat) to `1` (very lively) and applies to the cloned voice; values near `0.5` use the faster Turbo model, and other values make the voice worker switch to the standard model.

A third optional setting chooses the mentor's standard voice, used when it has no recording or the voice worker is down:

```json
{ "voice": { "voice_id": "en-IN-NeerjaNeural", "rate": "-10%", "pitch": "+5Hz" } }
```

`voice_id` must be one of the voices returned by `GET /voices`. This setting comes before the built-in `MENTOR_VOICES` table.

### Workers

Three optional tools give mentors a moving face and their own voice. Each runs in its own Python environment outside this repository, and the service keeps working without any of them.

| Tool         | File                        | Model        | What it does                                                   |
| ------------ | --------------------------- | ------------ | -------------------------------------------------------------- |
| Image step   | `workers/portrait_maker.py` | LivePortrait | Turns mentor images into moving clips, once per image          |
| Face worker  | `workers/face_worker.py`    | MuseTalk     | Stays loaded and makes the mentor's mouth follow each sentence |
| Voice worker | `workers/voice_worker.py`   | Chatterbox   | Stays loaded and speaks text in the mentor's recorded voice    |

The two workers are small HTTP servers that listen on `127.0.0.1` only and accept file paths only inside `ai_service/`. Install each model by following its own README, then point these settings in `backend/.env` at its Python interpreter and folder:

```text
VOICE_WORKER_PYTHON=/opt/voice_clone/.venv/bin/python
FACE_WORKER_PYTHON=/opt/lip_sync/.venv/bin/python
MUSETALK_DIR=/opt/lip_sync/MuseTalk
PORTRAIT_PYTHON=/opt/portrait/.venv/bin/python
LIVEPORTRAIT_DIR=/opt/portrait/LivePortrait
```

A GPU with 8 GB of memory runs both workers together (see "Sharing one GPU" for the AI model beside them). For faster lip-sync, also place the TAESD decoder (`madebyollin/taesd`) in `MuseTalk/models/taesd`; without it the full decoder is used.

### Sharing one GPU

The local AI model, the voice worker and the face worker together need about 9 GB while all three work. On an 8 GB GPU they therefore take turns: the reply is written, then each sentence is voiced, then lip-synced, and background work waits for a live question. Three settings in `backend/.env` tune this:

| Setting                 | Default | What it does                                                                                                                                    |
| ----------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `OLLAMA_GPU_LAYERS`     | all     | Layers of `OLLAMA_MODEL` on the GPU. `18` for `gemma3:latest` keeps it loaded beside both workers on 8 GB (slower writing, far sooner speaking) |
| `OLLAMA_KEEP_LOADED_GB` | `4`     | A local model larger than this is unloaded after each reply so the workers get the GPU                                                          |
| `MENTOR_GPU_OVERLAP`    | off     | `true` on 12 GB or more: the voice starts on the first sentence while the model still writes, and voice and face overlap                        |

### Lesson packs

A lesson pack is a lesson prepared ahead of time: a script (written by `LESSON_SCRIPT_MODEL` when it is installed, else by `OLLAMA_MODEL`) plus one audio file and one talking clip per sentence. Making a pack uses the GPU in the background; playing it uses none and starts at once. A question asked during a prepared lesson is answered live, with the lesson's script at the start of the prompt.

Packs are stored in `outputs/lesson_packs/<mentor>/` (not in git) and named after what they were made from, so a new personality rewrites scripts, a new voice recording re-voices them, new photos only re-make the clips, and anything already made is kept when preparation stops and resumes.

### Voice bank

When a mentor is set up, about 500 phrases from `backend/data/phrases.txt` are recorded once in its voice, with lip-sync: openers ("Great question."), transitions, checks, encouragement, closers, and short sentences with technical words. Every sentence the mentor then says in a live answer is kept as well. A sentence the bank already holds plays at once without the GPU, so answers start sooner and get faster the more the mentor is used. The AI is told which recorded openers it may begin an answer with.

Recording runs in the background after lessons being prepared and between live questions; the Studio shows its progress and can start or stop it. The bank shares the lesson packs' folder and media, and sentences are matched ignoring case, spacing and quote style. Add or change phrases in `phrases.txt`; only new ones are recorded.

### Mentor kits

A kit is one `.zip` with a mentor's folder and everything made from it (voice bank and prepared lessons). "Save as a kit" in the Studio writes `mentor_kits/<name>.zip` next to `ai_service`; copy it into the same folder on another computer and press "Import kits" (kits are also imported when the app starts). Media is named after the content of the voice and clips, not file dates, so everything in a kit plays at once on the new computer. Kits contain the voice recording, so they are never sent through the web page and the folder is not in git.

### Fallbacks

Every step has a backup, so a failure lowers quality instead of stopping the mentor:

| Step                   | First choice          | Then                     | Last resort                        |
| ---------------------- | --------------------- | ------------------------ | ---------------------------------- |
| Reply text             | Gemini                | Groq                     | A fixed apology line               |
| Voice                  | Mentor's cloned voice | Stock voice (Edge TTS)   | Text only                          |
| Face in a conversation | Lip-synced clip       | Audio over the idle clip |                                    |
| Lesson video           | D-ID                  | Lip-synced clips         | Clip library, then one looped clip |

A worker that cannot be reached is skipped for 20 seconds before it is tried again.

### Access control

The service has no login of its own, so keep it on your own machine or behind the Node API. It still limits what an outsider can do:

- **Other websites cannot make changes.** A state-changing request with an `Origin` header is refused unless the origin is the local website or listed in `ALLOWED_ORIGINS` (comma-separated). Server-side callers send no `Origin` and are not affected.
- **Rate limits per client address:** 30 conversation turns a minute, and 30 mentor changes an hour.
- **Uploads:** capped at about 150 MB per request before they are read, 15 MB per photo, 30 MB per recording, and 50 mentors in total. Vector images and images wider or taller than 12,000 pixels are refused.

### Starting everything with one command

```bash
cd ai_service
.venv/bin/python start_app.py
```

`start_app.py` turns new mentor images into clips, then starts the API, the website (when `frontend/` is installed and has a `.env`), the face worker, and the voice worker. It restarts a process that crashes, up to five times a minute, and then leaves it off so the fallbacks take over. Open `http://localhost:5173/mentor` for the classroom, or `http://127.0.0.1:8000/app` for a basic page that needs no website.

## Running the service

Start only the AI service:

```bash
npm run dev:ai
```

Start the complete product, including User, Admin, and AI services:

```bash
npm run dev
```

The development runner:

- labels AI output with `[AI/API]`;
- reports `Startup` and `Health` timing;
- reuses a healthy AI process already listening on port `8000`;
- stops only processes started by the current terminal when `Ctrl+C` is pressed.

The local AI API is available at `http://127.0.0.1:8000`. Interactive FastAPI documentation is available at `http://127.0.0.1:8000/docs`. The Node.js backend must use `AI_SERVICE_URL=http://127.0.0.1:8000`.

For direct debugging only, the underlying entry point is `ai_service/backend/graceful_server.py`.

## Main API routes

| Method   | Route                                  | Purpose                                                                                  |
| -------- | -------------------------------------- | ---------------------------------------------------------------------------------------- |
| `GET`    | `/`                                    | Basic service response used for readiness checks                                         |
| `POST`   | `/generate`                            | Queue lesson script, narration, video, and subtitle generation                           |
| `POST`   | `/generate-quiz`                       | Generate a multiple-choice quiz from lesson text                                         |
| `POST`   | `/explain-code`                        | Explain, debug, or optimize submitted code                                               |
| `GET`    | `/status/{job_id}`                     | Return generation status and generated asset metadata                                    |
| `GET`    | `/transcript/{filename}`               | Return a generated transcript                                                            |
| `POST`   | `/generate-syllabus`                   | Generate a structured course syllabus                                                    |
| `POST`   | `/generate-flashcards`                 | Generate study flashcards from a lesson transcript                                       |
| `GET`    | `/voices`                              | Return available narration voices                                                        |
| `GET`    | `/video-stream/{filename}`             | Serve a local generated video when cloud delivery is unavailable                         |
| `GET`    | `/transcript-stream/{filename}`        | Serve a local transcript file                                                            |
| `POST`   | `/talk`                                | Live conversation: streams the mentor's reply sentence by sentence as Server-Sent Events |
| `GET`    | `/talk/status`                         | Report whether the voice and face workers are running                                    |
| `POST`   | `/lesson-packs/prepare`                | Prepare lessons for a mentor in the background (`urgent` ones first)                     |
| `POST`   | `/lesson-packs/status`                 | Progress of a mentor's lesson packs: ready, waiting, time left, size on disk             |
| `POST`   | `/lesson-packs/pack`                   | The sentences and clips of one prepared lesson, ready to play                            |
| `DELETE` | `/lesson-packs/{mentor}/queue`         | Stop preparing a mentor's lessons; finished ones are kept                                |
| `GET`    | `/pack-media/{mentor}/{file}`          | Serve one audio file or clip of a lesson pack or the voice bank                          |
| `GET`    | `/mentors/{name}/voice-bank`           | Voice bank progress: phrases recorded, sentences kept from answers, size                 |
| `POST`   | `/mentors/{name}/voice-bank`           | Record the mentor's phrases in the background                                            |
| `DELETE` | `/mentors/{name}/voice-bank/recording` | Stop recording phrases; what is recorded is kept                                         |
| `POST`   | `/mentors/{name}/kit`                  | Save the mentor and its voice bank to `mentor_kits/<name>.zip`                           |
| `POST`   | `/mentor-kits/import`                  | Import the kits in the `mentor_kits` folder                                              |
| `GET`    | `/mentors`                             | List mentors with their voice type and preparation state                                 |
| `POST`   | `/mentors`                             | Create a mentor from uploaded images and an optional voice recording                     |
| `GET`    | `/mentors/{name}`                      | Return one mentor's settings and photo names for editing                                 |
| `PATCH`  | `/mentors/{name}`                      | Edit an uploaded mentor: personality, delivery, standard voice, photos, voice recording  |
| `GET`    | `/mentors/{name}/images/{file}`        | Serve one of the mentor's photos                                                         |
| `DELETE` | `/mentors/{name}`                      | Remove a mentor that was created by upload                                               |
| `GET`    | `/mentors/{name}/idle.mp4`             | Serve the mentor's idle clip                                                             |
| `GET`    | `/app`                                 | Basic live-conversation page                                                             |

Use the running service's FastAPI documentation for the current request and response schemas.

### Filtering voices

`GET /voices` returns every registered voice by default. Narrow the list with optional query parameters:

| Parameter  | Example             | Behavior                             |
| ---------- | ------------------- | ------------------------------------ |
| `language` | `?language=Spanish` | Only voices whose `language` matches |
| `gender`   | `?gender=Female`    | Only voices whose `gender` matches   |

- Matching is case-insensitive, so `?language=spanish` and `?language=Spanish` are equivalent.
- When both parameters are given, a voice must match both: `?language=English&gender=Male`.
- A value that matches no voice is not an error. The service responds with HTTP 200 and `{"voices": []}`.
- An empty parameter (`?language=`) is treated as if it were omitted.

```bash
curl "http://localhost:8000/voices?language=English&gender=Female"
```

## Generation workflow

1. Validate the lesson request.
2. Generate the lesson script with Gemini, in the mentor's personality when the pack defines one.
3. Use Groq only when it is configured and the primary provider fails.
4. Save the transcript text.
5. Generate narration: the mentor's cloned voice when the pack has a voice sample and the voice worker is running, otherwise Edge TTS.
6. Attempt D-ID rendering when its configuration is available.
7. When D-ID is unavailable or fails, render the mentor's clips with lip-sync through the face worker. If that is unavailable too, join the clip library, or loop the single clip, with FFmpeg.
8. Generate subtitle files.
9. Upload video and subtitles to Cloudinary when the full Cloudinary configuration is available.
10. Return asset metadata and remove temporary local media after successful cloud delivery.

The Node.js backend uses Redis or Memurai to queue and track video-generation work.

## Logging and sensitive data

The AI service uses Python's standard logging system. Configure logging verbosity through the `AI_SERVICE_LOG_LEVEL` environment variable in the AI service environment. The default level is `INFO`.

For example, add the following to `ai_service/backend/.env`:

```env
AI_SERVICE_LOG_LEVEL=INFO
```

Supported levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.

### Logging safety

- Do not log full request payloads, user learning preferences, generated lesson scripts, or other user-provided content.
- Do not log raw provider exception messages, which may contain request or response data.
- Prefer safe operational metadata, such as job identifiers, provider names, processing stages, statuses, and word counts.
- Keep failure messages generic; do not expose internal exception details to API clients.
- Review both log messages and their metadata for sensitive values when adding logging statements.
- Add log-capture regression tests when changing logging or error handling.

## Testing

Run the focused AI-service tests from the repository root with the isolated environment:

```bash
ai_service\.venv\Scripts\python.exe -m pytest -q ai_service/tests
```

For API exploration, start the service and open the FastAPI documentation at `http://127.0.0.1:8000/docs`.

## Troubleshooting

| Problem                                | Resolution                                                                                                             |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| AI environment file is missing         | Copy `ai_service/backend/.env.example` to `ai_service/backend/.env`, add `GEMINI_API_KEY`, then run `npm run setup:ai` |
| `GEMINI_API_KEY` is not configured     | Add a real Gemini key without sharing it, then rerun setup                                                             |
| FFmpeg is unavailable                  | Install FFmpeg, ensure it is on `PATH`, restart the terminal, and run `npm run setup:ai`                               |
| Redis or Memurai warning               | Start a Redis-compatible service on port `6379`; queued video generation requires it                                   |
| D-ID configuration is missing          | The service uses the local FFmpeg fallback renderer                                                                    |
| Cloudinary configuration is incomplete | Cloud delivery is disabled and the supported local delivery path is used                                               |
| Video remains in processing state      | Review `[AI/API]` output and the User API queue logs for the first failed generation step                              |

## Security notes

Lesson, syllabus, quiz, and flashcard generation send fixed task instructions in
the provider's system-instruction field and caller-controlled values as a
separate, JSON-serialized user message. The system instructions tell the model
to treat JSON values as untrusted content rather than instructions. JSON
serialization preserves input as data but cannot guarantee resistance to every
prompt injection.

Lesson preferences are validated against the supported fields
`explanation_type`, `learning_style`, `teaching_pace`, `example_type`, and
`focus_area`; unknown keys are rejected.

- Never commit `.env` files, provider keys, database credentials, or generated authentication tokens.
- Do not print raw request objects, generated secrets, full credential values, or provider authorization headers.
- Keep celebrity source selection restricted to validated files inside the configured input directory.
