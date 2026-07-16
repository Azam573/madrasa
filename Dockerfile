# ════════════════════════════════════════════════════════════════
# Dockerfile — Smart Madrasa ERP v9.0
# Multi-stage build: dependencies → production image
#
# Security fix (v9.0):
# - Non-root user 'madrasa' (UID 1000) দিয়ে চালানো হচ্ছে
# - Root হিসেবে চললে container compromise = host root access
# - /app directory ownership non-root user-এ দেওয়া হয়েছে
# - /app/uploads ও /app/logs writeable করা হয়েছে
# ════════════════════════════════════════════════════════════════

FROM python:3.11-slim AS base

# ── System dependencies ──────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user তৈরি ──────────────────────────────────────────
# UID/GID 1000 — standard non-root user
RUN groupadd --gid 1000 madrasa \
 && useradd  --uid 1000 --gid madrasa \
             --shell /bin/bash \
             --create-home \
             madrasa

# ── Working directory ────────────────────────────────────────────
WORKDIR /app

# ── Python packages — root হিসেবে install (permission দরকার) ───
# Lockfile (exact versions) — reproducible builds;
# আপডেট করতে: pip-compile requirements.txt -o requirements.lock
COPY requirements.lock requirements_api.lock ./
RUN pip install --no-cache-dir \
    -r requirements.lock \
    -r requirements_api.lock

# ── Application code copy ────────────────────────────────────────
COPY . .

# ── Writable directories তৈরি ───────────────────────────────────
# uploads, logs — non-root user থেকে লেখার অনুমতি দরকার
RUN mkdir -p /app/uploads /app/logs \
 && chown -R madrasa:madrasa /app

# ── Non-root user-এ switch ──────────────────────────────────────
USER madrasa

EXPOSE 8000 8501

# Default command
CMD ["sh", "start.sh"]
