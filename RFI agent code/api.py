"""
FastAPI backend for the RFI Agent.
Exposes the pipeline (parse → fill → review → download) over HTTP.
"""

import asyncio
import json
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from excel_parser import parse_rfi, extract_client_from_filename, RFIQuestion
from agents import match_and_fill_async, review_answers
from writer import write_filled_rfi

load_dotenv()

app = FastAPI(title="RFI Agent API", version="0.1.0")

# CORS — allow the Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store (maps session_id → session data)
# Production would use Redis or DB; fine for single-user local tool.
_sessions: dict[str, dict] = {}

UPLOAD_DIR = Path(tempfile.gettempdir()) / "rfi_agent_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


# ─── ROUTES ─────────────────────────────────────────────────────────────────


@app.post("/api/upload")
async def upload_file(file: UploadFile):
    """
    Upload an Excel RFI file. Returns parsed questions + session ID.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Only .xlsx and .xlsm files are supported")

    # Save to temp location
    session_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    saved_path = UPLOAD_DIR / f"{session_id}{ext}"

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(413, "File too large (max 50MB)")

    saved_path.write_bytes(content)

    # Parse
    try:
        rfi_questions: list[RFIQuestion] = parse_rfi(str(saved_path))
    except Exception as e:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(422, f"Failed to parse Excel file: {e}")

    if not rfi_questions:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(422, "No questions found in this file")

    client_name = extract_client_from_filename(file.filename)

    # Convert to dicts for JSON serialization + pipeline consumption
    questions = [asdict(q) for q in rfi_questions]
    for q in questions:
        q["status"] = "pending"
        q["generated_answer"] = ""
        q["confidence"] = None
        q["citation"] = ""
        q["fill_status"] = ""

    # Store session
    _sessions[session_id] = {
        "file_path": str(saved_path),
        "file_name": file.filename,
        "client_name": client_name,
        "questions": questions,
        "status": "parsed",
    }

    return {
        "session_id": session_id,
        "file_name": file.filename,
        "client_name": client_name,
        "question_count": len(questions),
        "questions": questions,
    }


@app.get("/api/fill/{session_id}")
async def fill_questions(session_id: str):
    """
    Run the Matcher+Filler agent on all questions.
    Returns a Server-Sent Events stream with progress updates.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["status"] == "filling":
        raise HTTPException(409, "Fill already in progress")

    session["status"] = "filling"

    async def event_generator():
        questions = session["questions"]
        client_name = session["client_name"]

        # Progress callback — fires when each question status changes
        async def on_progress(q: dict):
            yield {
                "event": "progress",
                "data": json.dumps({
                    "index": questions.index(q),
                    "status": q.get("fill_status", "pending"),
                    "generated_answer": q.get("generated_answer", ""),
                    "confidence": q.get("confidence"),
                    "citation": q.get("citation", ""),
                    "source_references": q.get("source_references", []),
                }),
            }

        # We can't pass a generator-yielding callback directly to match_and_fill_async.
        # Instead, use a queue to bridge async progress events to SSE.
        progress_queue: asyncio.Queue = asyncio.Queue()

        async def progress_callback(q: dict):
            await progress_queue.put(q)

        # Run fill in background task
        fill_task = asyncio.create_task(
            match_and_fill_async(
                questions,
                client_name=client_name,
                on_progress=progress_callback,
            )
        )

        # Stream progress events as they arrive
        completed = 0
        total = len(questions)

        while completed < total or not fill_task.done():
            try:
                q = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                idx = questions.index(q)
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "index": idx,
                        "status": q.get("fill_status", "pending"),
                        "generated_answer": q.get("generated_answer", ""),
                        "confidence": q.get("confidence"),
                        "citation": q.get("citation", ""),
                    }),
                }
                if q.get("fill_status") in ("filled", "error", "rate_limited", "parse_error", "truncated"):
                    completed += 1
            except asyncio.TimeoutError:
                # Send keepalive
                if fill_task.done():
                    break
                yield {"event": "keepalive", "data": ""}

        # Ensure task is awaited (propagates exceptions)
        await fill_task

        session["status"] = "filled"
        yield {
            "event": "done",
            "data": json.dumps({"filled": total}),
        }

    return EventSourceResponse(event_generator())


@app.post("/api/review/{session_id}")
async def run_review(session_id: str):
    """
    Run the Reviewer agent on filled questions.
    Returns the reviewed questions with review_status fields.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["status"] not in ("filled", "reviewed"):
        raise HTTPException(400, "Questions must be filled before review")

    questions = session["questions"]

    # Run reviewer (sync, wrapped in executor to not block)
    loop = asyncio.get_event_loop()
    reviewed = await loop.run_in_executor(None, review_answers, questions)

    session["questions"] = reviewed
    session["status"] = "reviewed"

    return {
        "session_id": session_id,
        "questions": reviewed,
    }


@app.get("/api/download/{session_id}")
async def download_filled(session_id: str):
    """
    Generate and download the filled Excel file.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["status"] not in ("filled", "reviewed"):
        raise HTTPException(400, "Questions must be filled before download")

    source_path = session["file_path"]
    questions = session["questions"]

    # Generate the filled file
    try:
        output_path = write_filled_rfi(source_path, questions)
    except Exception as e:
        raise HTTPException(500, f"Failed to generate filled file: {e}")

    basename = Path(session["file_name"]).stem
    ext = Path(session["file_name"]).suffix

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{basename}_FILLED{ext}",
    )


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    """Get current session state (questions + status)."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return {
        "session_id": session_id,
        "file_name": session["file_name"],
        "client_name": session["client_name"],
        "status": session["status"],
        "questions": session["questions"],
    }


@app.get("/api/health")
async def health():
    """Health check."""
    from agents import _azure_configured
    azure_ok = False
    try:
        azure_ok = _azure_configured()
    except Exception:
        pass

    return {
        "status": "ok",
        "azure_configured": azure_ok,
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }
