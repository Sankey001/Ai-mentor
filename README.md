# AI Mentor (beta)

## Run with Docker

```bash
docker build -t ai-mentor .
docker run --rm -p 127.0.0.1:5173:5173 -p 127.0.0.1:8000:8000 \
  -e GEMINI_API_KEY=your_key -v ai-mentor-data:/data ai-mentor
```

Open http://localhost:5173/mentor

Get a free Gemini key at https://aistudio.google.com/apikey

## Run without Docker

```bash
cd ai_service
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn edge-tts google-genai groq httpx mutagen requests \
  python-dotenv "cachetools<6" "cloudinary<2" python-multipart pillow pillow-heif pytest pytest-asyncio
cp backend/.env.example backend/.env      # then set GEMINI_API_KEY in backend/.env

cd ../frontend
npm ci
cp .env.example .env                      # then set VITE_MENTOR_PREVIEW=true

cd ../ai_service
.venv/bin/python start_app.py
```

Open http://localhost:5173/mentor

Needs Python 3.10+, Node.js 20+ and FFmpeg.

## Stop

Press `Ctrl+C`.
