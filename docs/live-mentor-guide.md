# AI Mentor: how to use it

This guide covers the live mentor on this machine: starting it, learning with it, creating your own mentor, and fixing common problems. Paths below are relative to the repository root.

## 1. Start and stop

**Start** (one command starts everything):

```bash
cd ai_service
.venv/bin/python start_app.py
```

Then open **http://localhost:5173/mentor**.

- The page opens within a few seconds.
- The mentor's lip-sync and own voice switch on about 30–60 seconds later, once the models have loaded.
- The first start after adding a mentor takes a few minutes longer.

**Stop:** press `Ctrl+C` in that terminal. If it was started in the background:

```bash
pkill -INT -f '^\.venv/bin/python start_app\.py'
```

## 2. Before the first real use

Add an AI key, or the mentor only says "Sorry, I lost my train of thought":

1. Get a free Gemini key at https://aistudio.google.com/apikey.
2. Open `ai_service/backend/.env` and put it after `GEMINI_API_KEY=`.
3. Restart the app.

A Groq key (`GROQ_API_KEY=`) works as a backup if Gemini is unavailable.

## 3. The classroom page (`/mentor`)

| What you want             | What to do                                                         |
| ------------------------- | ------------------------------------------------------------------ |
| Pick a chapter or lesson  | Click **Course contents** at the top left; search or open a module |
| Have the lesson explained | Click **Teach this lesson**                                        |
| Ask a question            | Type it and press Enter, or click the microphone (Chrome and Edge) |
| Get a quick follow-up     | Click a chip: example, simpler, or quiz                            |
| Move on                   | Use the arrows beside **Teach this lesson**                        |
| Interrupt the mentor      | Click **Stop**                                                     |
| Change mentor             | Use the mentor drop-down in the toolbar                            |
| Open another course       | Go to `/mentor/2` or `/mentor/3`                                   |

The two indicators in the toolbar show what the current mentor is using:

- **Mentor's own voice / Standard voice:** whether the mentor speaks in its recorded voice.
- **Lip-sync on / off:** whether the mouth moves with the speech. When off, you hear the mentor over its idle clip.

Under each reply, a small line shows what was used for that reply, for example `Voice: cloned · Face: lipsync`.

## 4. Create your own mentor (`/mentor-studio`)

1. Open **Create a mentor** from the classroom, or go to http://localhost:5173/mentor-studio.
2. **Name:** lowercase letters, digits, `-` or `_`, for example `asha`.
3. **Photos:** drop 1 to 8 images. Any image format works.
4. **Voice (optional):** upload a recording, or click **Record now** and speak.
5. **Personality (optional):** describe how the mentor talks.
6. **Standard voice (optional):** pick one of the ten voices and set its speed and pitch. It is used when the mentor has no recording of its own, or when the own voice is unavailable.
7. **Delivery:** slide between calm and lively. This shapes the mentor's own voice.
8. Tick the permission box and click **Create mentor**.

The mentor shows as **Preparing** in the list. Preparation took about two minutes for a two-photo mentor in testing, and you can leave the page. When it shows **Ready**, it appears in the classroom's mentor drop-down.

To remove a mentor you uploaded, click the bin icon beside it.

### Editing a mentor

Click the pencil icon beside a mentor you uploaded. You can then:

- **Change the personality, delivery and standard voice.** These take effect on the next reply.
- **Replace or remove the voice recording.**
- **Add or remove photos.** Click the cross on a photo to mark it for removal, and again to keep it. A mentor must keep at least one photo. Changing photos prepares the mentor again, which takes a few minutes.

Click **Save changes**, or **Cancel** to leave the mentor as it was. The name cannot be changed; to rename, create a new mentor. Adding new photos or a new recording asks for the permission tick again.

### Getting a good result

- **Photos:**
  - Use clear, front-facing shots of one person, with the mouth closed.
  - Keep the face at least 500 pixels wide.
  - Use similar lighting and framing across photos, so the pose changes between sentences look natural.
  - Avoid full-body shots, since the face ends up small and blurry.
- **Voice:**
  - Record 10 to 30 seconds of clear speech, with no music or background noise.
  - Speak the way you want the mentor to sound; the accent and style carry over.
- **Delivery:** "balanced" uses the fastest voice model. Calm or lively sounds more expressive but takes about twice as long per sentence.
- **Personality:** two or three sentences work best, for example: "Calm and patient. Short sentences. Likes everyday comparisons."
- **Permission:** only use the face and voice of someone who has agreed to it.

### Creating a mentor by hand

You can also make a folder yourself in `ai_service/backend/input/<name>/` and put in it:

- images in any format;
- `voice.wav`, the voice recording;
- `mentor.json`, for example `{"persona": "Calm and patient.", "expressiveness": 0.5}`.

Restart the app and it prepares the mentor. Mentors made this way cannot be deleted from the studio page; delete the folder instead.

## 5. What happens when something fails

The app keeps working with lower quality instead of stopping:

| If this is unavailable   | You get                                           |
| ------------------------ | ------------------------------------------------- |
| Gemini                   | Groq answers; if both fail, a fixed apology line  |
| The mentor's own voice   | A standard voice                                  |
| All voices (no internet) | The reply as text with captions                   |
| Lip-sync                 | The voice over the mentor's idle clip             |
| A worker crashes         | It restarts on its own, up to five times a minute |

## 6. Troubleshooting

| Problem                                    | Fix                                                                                                                                                                                           |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "The mentor service is not reachable"      | Start the app (section 1); the page reconnects on its own                                                                                                                                     |
| Mentor only apologises                     | Add the Gemini key (section 2)                                                                                                                                                                |
| "Lip-sync off" for a long time             | Wait a minute after starting; check the terminal for `[face]` errors                                                                                                                          |
| A new mentor shows **Failed**              | The reason is shown under its name; usually no clear face was found, so try a sharper front-facing photo                                                                                      |
| First reply after switching mentor is slow | Normal: 7–9 seconds once, then about 3.5 seconds. Up to about 17 seconds if the new mentor's delivery is calm or lively and the previous one was balanced, because the voice model is swapped |
| Mouth looks blurry                         | Use larger, sharper photos                                                                                                                                                                    |
| Microphone button missing                  | Your browser does not support speech input; use Chrome or Edge, or type                                                                                                                       |
| Port already in use                        | Stop the old app first (section 1)                                                                                                                                                            |

## 7. Good to know

- **Sign-in is switched off for this local preview** by `VITE_MENTOR_PREVIEW=true` in `frontend/.env`. Remove that line to put the mentor pages behind the site's normal login.
- **The course content is the project's bundled sample** (three courses), because the main database and API are not set up on this machine. Signed in against the real backend, the same page loads the learner's real course.
- **A basic page without the website** is at http://127.0.0.1:8000/app.
- **Speed on this laptop's GPU:** a sentence is voiced in about 1.5 seconds and lip-synced in about 2 seconds.
- **Developer details** (settings, workers, endpoints) are in `ai_service/README.md`; a Docker quick start is in the root `README.md`.
