# AI Mentor live mentor: website + AI service in one container.
#
#   docker build -t ai-mentor .
#   docker run --rm -p 127.0.0.1:5173:5173 -p 127.0.0.1:8000:8000 \
#       -e GEMINI_API_KEY=your_key -v ai-mentor-data:/data ai-mentor
#
# Then open http://localhost:5173/mentor
#
# This image has no GPU models. Mentors speak in a standard voice and their
# photo moves with a slow zoom; lip-sync and cloned voices need the model
# environments described in ai_service/README.md.

FROM node:22-bookworm-slim AS node

FROM python:3.13-slim-bookworm

# Node.js for the website, FFmpeg for audio and video.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home mentor \
    && mkdir -p /app/Ai_Mentor /data \
    && chown mentor:mentor /app/Ai_Mentor /data

WORKDIR /app/Ai_Mentor

# Dependencies first, so code changes do not reinstall them.
RUN pip install --no-cache-dir \
    "fastapi>=0.143.1,<1.0.0" "uvicorn>=0.30.0,<1.0.0" "edge-tts>=7.3.1,<8.0.0" \
    "google-genai>=2.33.0,<3.0.0" "groq>=0.9.0,<1.0.0" httpx "mutagen>=1.48.1,<2.0.0" \
    "requests>=2.34.2,<3.0.0" "python-dotenv>=1.2.4,<2.0.0" "cachetools>=5.3.0,<6.0.0" \
    "cloudinary>=1.46.3,<2.0.0" "python-multipart>=0.0.20,<1.0.0" \
    "pillow>=11.0.0" "pillow-heif>=0.20.0"

# Everything from here on runs as an ordinary user, not root.
USER mentor
COPY --chown=mentor:mentor frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --no-audit --no-fund

COPY --chown=mentor:mentor ai_service/ ai_service/
COPY --chown=mentor:mentor frontend/ frontend/

# Website settings. Firebase values are placeholders: the mentor pages open
# without sign-in in this preview image.
RUN printf '%s\n' \
    'VITE_API_BASE_URL=http://localhost:5000' \
    'VITE_FIREBASE_API_KEY=container-preview-key' \
    'VITE_FIREBASE_AUTH_DOMAIN=container-preview.firebaseapp.com' \
    'VITE_FIREBASE_PROJECT_ID=container-preview' \
    'VITE_FIREBASE_STORAGE_BUCKET=container-preview.appspot.com' \
    'VITE_FIREBASE_MESSAGING_SENDER_ID=000000000000' \
    'VITE_FIREBASE_APP_ID=1:000000000000:web:0000000000000000000000' \
    'VITE_AI_SERVICE_URL=http://127.0.0.1:8000' \
    'VITE_MENTOR_PREVIEW=true' > frontend/.env

# Mentors and generated media live in /data, so they survive a new container.
RUN mkdir -p /data/input /data/outputs \
    && cp -rn ai_service/backend/input/. /data/input/ \
    && rm -rf ai_service/backend/input ai_service/outputs \
    && ln -s /data/input ai_service/backend/input \
    && ln -s /data/outputs ai_service/outputs
VOLUME /data

# The workers are not in this image; do not wait on them.
ENV VOICE_WORKER_URL="" \
    FACE_WORKER_URL="" \
    PYTHONUNBUFFERED=1

EXPOSE 5173 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/mentors', timeout=4)"

WORKDIR /app/Ai_Mentor/ai_service
CMD ["python", "start_app.py", "--host", "0.0.0.0"]
