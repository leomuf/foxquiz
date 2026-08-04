# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ==============================================================================
# Modified and extended by Leonardo Muffato (AUTOSOFT Engineering - www.autosoft-engineering.de) 2026.
# Copyright (c) 2026 Leonardo Muffato (AUTOSOFT Engineering - www.autosoft-engineering.de).
# All custom application additions, security checkpoints, and middleware components
# are licensed under CC BY 4.0. See global LICENSE file for details.
# ==============================================================================

import logging as python_logging
import os
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as google_cloud_logging

from app.app_utils.callbacks import SecurityBlockException
from app.app_utils.request_context import (
    anonymous_id_ctx,
    client_ip_ctx,
    client_locale_ctx,
)
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.database.firestore_repo import FirestoreRepository

setup_telemetry()

# Configure standard python logging
python_logging.basicConfig(level=python_logging.INFO)


class FallbackLogger:
    def __init__(self):
        self._logger = python_logging.getLogger(__name__)

    def log_struct(self, info: dict, severity: str = "INFO"):
        msg = f"[{severity}] {info}"
        if severity in ("WARNING", "ERROR", "CRITICAL"):
            self._logger.warning(msg)
        else:
            self._logger.info(msg)

    def info(self, msg: str, *args, **kwargs):
        self._logger.info(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._logger.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._logger.warning(msg, *args, **kwargs)


class SafeLogger:
    def __init__(self, real_logger, fallback):
        self.real_logger = real_logger
        self.fallback = fallback
        self.use_fallback = False

    def log_struct(self, info: dict, severity: str = "INFO"):
        if not self.use_fallback:
            try:
                self.real_logger.log_struct(info, severity=severity)
                return
            except Exception as e:
                python_logging.warning(
                    f"Cloud Logging write failed ({e}). Falling back to standard logging."
                )
                self.use_fallback = True
        self.fallback.log_struct(info, severity=severity)

    def info(self, msg: str, *args, **kwargs):
        self.fallback.info(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self.fallback.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self.fallback.warning(msg, *args, **kwargs)


try:
    if os.getenv("INTEGRATION_TEST") == "TRUE":
        raise ValueError("Mock mode requested for integration tests")
    logging_client = google_cloud_logging.Client()
    real_logger = logging_client.logger(__name__)
    logger = SafeLogger(real_logger, FallbackLogger())
except Exception as e:
    logger = FallbackLogger()
    python_logging.warning(f"Using FallbackLogger due to: {e}")

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

# Artifact bucket for ADK (created by Terraform, passed via env var)
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# In-memory session configuration - no persistent storage
session_service_uri = None

artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=True,
)
app.title = "foxquiz"
app.description = "API for interacting with the Agent foxquiz"

# Dynamically remove the default "/" route registered by ADK to expose our own UI
# Modify app.routes list in-place because it has no setter in newer FastAPI/Starlette versions.
for r in list(app.routes):
    if getattr(r, "path", None) == "/":
        app.routes.remove(r)

# Mount static files and serve SPA frontend
static_dir = os.path.join(AGENT_DIR, "app", "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def read_root(request: Request):
    # Determine user language
    lang = "en"  # Default
    query_lang = request.query_params.get("lang")
    if query_lang:
        query_lang = query_lang.lower()
        if query_lang in ["de", "pt", "en"]:
            lang = query_lang
    else:
        accept_language = request.headers.get("Accept-Language", "").lower()
        if "pt" in accept_language:
            lang = "pt"
        elif "de" in accept_language:
            lang = "de"

    # Social Preview Meta Tags Meta Data
    meta_data = {
        "de": {
            "title": "FoxQuiz 🦊 — Dein spielerischer Lernbegleiter!",
            "description": "Intelligente, maskottchengeführte Prüfungsvorbereitung für die Klassen 5-12. Kostenlos, sicher, werbefrei und perfekt auf den Lehrplan abgestimmt!",
        },
        "pt": {
            "title": "FoxQuiz 🦊 — Seu companheiro de estudos divertido!",
            "description": "Preparação inteligente para avaliações guiada por mascotes da 5º série até o 3º ano do ensino médio. Gratuito, seguro, sem anúncios e alinhado ao currículo escolar!",
        },
        "en": {
            "title": "FoxQuiz 🦊 — Your Playful Exam Prep Companion!",
            "description": "Intelligent, mascot-guided exam preparation for grades 5-12. Free, safe, ad-free, and perfectly aligned with school curriculums!",
        },
    }

    selected = meta_data[lang]

    # Generate the Open Graph/Twitter Meta Tags
    og_tags = f"""
    <!-- Open Graph / Facebook / WhatsApp -->
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://foxquiz.app/">
    <meta property="og:title" content="{selected["title"]}">
    <meta property="og:description" content="{selected["description"]}">
    <meta property="og:image" content="https://foxquiz.app/static/assets/foxquiz_github_social_preview.jpg">

    <!-- Twitter / X -->
    <meta property="twitter:card" content="summary_large_image">
    <meta property="twitter:url" content="https://foxquiz.app/">
    <meta property="twitter:title" content="{selected["title"]}">
    <meta property="twitter:description" content="{selected["description"]}">
    <meta property="twitter:image" content="https://foxquiz.app/static/assets/foxquiz_github_social_preview.jpg">
    """

    # Read the base index.html file
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse("index.html not found", status_code=404)

    with open(index_path, encoding="utf-8") as f:
        html_content = f.read()

    # Dynamically inject localized HTML lang attribute
    html_content = html_content.replace('<html lang="en">', f'<html lang="{lang}">')

    # Dynamically inject localized Title
    html_content = html_content.replace(
        "<title>FoxQuiz — Your Playful Exam Prep Companion!</title>",
        f"<title>{selected['title']}</title>",
    )

    # Dynamically inject the Social Preview Meta Tags right before </head>
    html_content = html_content.replace("</head>", f"{og_tags}\n</head>")

    return HTMLResponse(html_content)


# --- ContextVar Injection Middleware ---
@app.middleware("http")
async def inject_request_metadata(request: Request, call_next):
    """Middleware to securely capture client IP, anonymous ID, and locale into ContextVars."""
    # 1. Resolve client IP (supporting standard proxy headers)
    client_ip = "127.0.0.1"
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[0].strip()
    elif request.client:
        client_ip = request.client.host

    # 2. Resolve Anonymous Visitor ID
    anon_id = request.headers.get("X-Anonymous-ID")
    if not anon_id:
        # Fallback to cookie or generate a transient one
        anon_id = request.cookies.get("anon_id") or f"transient_{uuid.uuid4().hex[:12]}"

    # 3. Resolve Locale / Preferred Language
    accept_language = request.headers.get("Accept-Language", "de")
    locale = "de"
    if "pt" in accept_language.lower() or request.headers.get("X-Locale") == "pt":
        locale = "pt"
    elif "en" in accept_language.lower() or request.headers.get("X-Locale") == "en":
        locale = "en"

    # Token bounds: Bind these values to the current async context
    t1 = client_ip_ctx.set(client_ip)
    t2 = anonymous_id_ctx.set(anon_id)
    t3 = client_locale_ctx.set(locale)

    try:
        response = await call_next(request)
        # If client does not have an anon_id cookie, set it
        if not request.cookies.get("anon_id") and not request.headers.get(
            "X-Anonymous-ID"
        ):
            response.set_cookie("anon_id", anon_id, max_age=365 * 24 * 3600)
        return response
    finally:
        # Clean up ContextVars to avoid memory leaks
        client_ip_ctx.reset(t1)
        anonymous_id_ctx.reset(t2)
        client_locale_ctx.reset(t3)


# --- Custom Exception Handlers ---
@app.exception_handler(SecurityBlockException)
async def handle_security_block(request: Request, exc: SecurityBlockException):
    """Exception handler to catch Safety Checkpoint blocks and return friendly responses."""
    logger.log_struct(
        {
            "event": "security_block",
            "message": exc.message,
            "block_type": exc.block_type,
            "anonymous_id": anonymous_id_ctx.get(),
            "client_ip": client_ip_ctx.get(),
        },
        severity="WARNING",
    )
    # Return 200 OK with a block status. This allows the child-friendly chat UI
    # to render the safety message inline without throwing technical error states.
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "blocked",
            "block_type": exc.block_type,
            "message": exc.message,
        },
    )


# --- Database-Backed API Endpoints ---


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect, persist, and aggregate user feedback in Firestore."""
    repo = FirestoreRepository()
    # Save the detailed log in Firestore
    log_id = repo.save_feedback_log(
        score=feedback.score,
        text=feedback.text or "",
        session_id=feedback.session_id,
        anonymous_id=feedback.user_id,
    )

    logger.log_struct(
        {
            "event": "feedback_saved",
            "log_id": log_id,
            "score": feedback.score,
            "session_id": feedback.session_id,
        },
        severity="INFO",
    )
    return {"status": "success", "log_id": log_id}


@app.post("/share")
def share_quiz(payload: dict) -> dict[str, Any]:
    """Freeze a generated quiz and persist it in the cloud for sharing."""
    quiz_data = payload.get("quiz_data")
    if not quiz_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required quiz_data payload.",
        )

    # Generate a secure, unique sharing identifier
    quiz_id = payload.get("quiz_id") or str(uuid.uuid4())
    repo = FirestoreRepository()
    success = repo.save_shared_quiz(quiz_id, quiz_data)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist shared quiz in storage.",
        )

    return {"status": "success", "quiz_id": quiz_id}


@app.get("/quiz/{quiz_id}")
def get_shared_quiz(quiz_id: str) -> dict[str, Any]:
    """Retrieve a frozen, pre-generated shared quiz by its ID (Zero-Token Cost)."""
    repo = FirestoreRepository()
    quiz_data = repo.get_shared_quiz(quiz_id)

    if not quiz_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shared quiz not found, or it has expired under GDPR cleanup policies.",
        )

    return {"status": "success", "quiz_data": quiz_data}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
