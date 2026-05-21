"""
FastAPI backend for the RFI Agent.
Exposes the pipeline (parse → fill → review → download) over HTTP.
"""

import asyncio
import json
import os
import random
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
from agents import match_and_fill, review_answers
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


def _sanitize_for_pdf(text: str) -> str:
    """Replace non-latin1 characters so Helvetica can render them."""
    replacements = {
        "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "-",
        "\u00a0": " ", "\u2010": "-", "\u2011": "-", "\u2012": "-",
        "\u200b": "", "\ufeff": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Fallback: encode to latin-1, replacing anything still unsupported
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _generate_pdf(questions: list[dict], file_name: str, client_name: str) -> str:
    """Generate a PDF report of filled RFI questions."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "RFI Agent - Filled Responses", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _sanitize_for_pdf(f"Source: {file_name}"), new_x="LMARGIN", new_y="NEXT")
    if client_name:
        pdf.cell(0, 6, _sanitize_for_pdf(f"Client: {client_name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Questions: {len(questions)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Group by sheet
    sheets = {}
    for q in questions:
        sheet = q.get("sheet_name", "Unknown")
        if sheet not in sheets:
            sheets[sheet] = []
        sheets[sheet].append(q)

    for sheet_name, sheet_qs in sheets.items():
        # Sheet header
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(68, 114, 196)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 8, _sanitize_for_pdf(f"  {sheet_name}"), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

        for i, q in enumerate(sheet_qs, 1):
            confidence = q.get("confidence") or 0
            answer = q.get("generated_answer", "")
            citation = q.get("citation", "")

            # Question
            pdf.set_font("Helvetica", "B", 10)
            q_text = _sanitize_for_pdf(f"Q{i}: {q.get('question_text', '')}")
            pdf.multi_cell(0, 5, q_text, new_x="LMARGIN", new_y="NEXT")

            # Answer with confidence color
            if confidence >= 0.8:
                pdf.set_fill_color(198, 239, 206)  # green
            elif confidence >= 0.5:
                pdf.set_fill_color(255, 235, 156)  # yellow
            else:
                pdf.set_fill_color(255, 199, 206)  # red

            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(0, 5, _sanitize_for_pdf(answer) or "[No answer generated]", fill=True, new_x="LMARGIN", new_y="NEXT")

            # Confidence + citation
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 100, 100)
            meta = f"Confidence: {int(confidence * 100)}%"
            if citation:
                meta += f"  |  Source: {_sanitize_for_pdf(citation)}"
            pdf.cell(0, 5, meta, new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(4)

    output_path = str(UPLOAD_DIR / f"{Path(file_name).stem}_FILLED.pdf")
    pdf.output(output_path)
    return output_path


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

    if session["status"] in ("filled", "reviewed"):
        raise HTTPException(409, "Session already filled. Upload a new file to start over.")

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

        # We can't pass a generator-yielding callback directly to match_and_fill.
        # Instead, use a queue to bridge progress events to SSE.
        progress_queue: asyncio.Queue = asyncio.Queue()

        async def progress_callback(q: dict):
            await progress_queue.put(q)

        # Run fill in background task (sync function wrapped in executor)
        loop = asyncio.get_event_loop()

        async def run_fill():
            # match_and_fill is sync and doesn't support on_progress callback
            # Run it in executor and post all results at once
            from indexer import load_knowledge_base
            from base_info_parser import load_base_info
            knowledge_base = load_knowledge_base()
            base_info = load_base_info()
            filled = await loop.run_in_executor(
                None, match_and_fill, questions, knowledge_base, base_info, client_name
            )
            # Update session questions and push progress for each
            for i, q in enumerate(filled):
                questions[i].update(q)
                questions[i]["fill_status"] = "filled"
                await progress_queue.put(questions[i])

        fill_task = asyncio.create_task(run_fill())

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


_MOCK_ANSWERS = [
    "Avalere Health is a strategic advisory company within Inizio, specializing in market access, policy, reimbursement, and evidence-based solutions for life sciences companies. Founded in 2000, headquartered in Washington, D.C., with offices in London, Manchester, New York, Dublin, Athens, and Singapore.",
    "Yes, Avalere Health maintains SOC 2 Type II certification and complies with GDPR requirements for all EU data subjects. Annual third-party audits are conducted by independent assessors.",
    "Avalere employs approximately 350 full-time staff globally with an attrition rate of 12% (2024). All staff complete mandatory compliance training annually including anti-bribery, data protection, and pharmacovigilance modules.",
    "Our anti-bribery and anti-corruption policy aligns with the UK Bribery Act 2010 and US FCPA. Annual training is mandatory for all employees. We maintain a gifts and hospitality register reviewed quarterly.",
    "Avalere maintains a Business Continuity Plan (BCP) tested annually. RPO: 4 hours, RTO: 24 hours for critical systems. All data backed up to geographically redundant Azure regions.",
    "Our Medical team has over 300 full-time team members located in the UK, Ireland, Greece and North America. Over 70% of our scientific staff hold life sciences doctorates, and over 60% have more than 10 years' experience.",
    "Avalere Health holds ISO 27001:2022 certification for information security management. Penetration testing is conducted bi-annually by CREST-certified third parties.",
    "[NEEDS REVIEW] Insufficient context to answer this question confidently. Please consult the relevant internal documentation or subject matter expert.",
]

_MOCK_CITATIONS = [
    "Company Information.html § Overview",
    "Compliance.html § SOC 2 Certification",
    "People Information.html § Headcount & Retention",
    "Compliance.html § Anti-Bribery Policy",
    "Data, information security.html § Business Continuity",
    "People Information.html § Medical Team",
    "Data, information security.html § ISO 27001",
    "",
]


@app.get("/api/fill-mock/{session_id}")
async def fill_questions_mock(session_id: str):
    """
    Mock fill — returns dummy answers via SSE without calling any LLM.
    Used for demo / development when APIs aren't configured.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["status"] == "filling":
        raise HTTPException(409, "Fill already in progress")

    if session["status"] in ("filled", "reviewed"):
        raise HTTPException(409, "Session already filled. Upload a new file to start over.")

    session["status"] = "filling"

    async def event_generator():
        questions = session["questions"]
        total = len(questions)

        # Simulate filling with random delays
        indices = list(range(total))
        random.shuffle(indices)

        for i, idx in enumerate(indices):
            # Small delay to simulate processing (10-50ms per question)
            await asyncio.sleep(random.uniform(0.01, 0.05))

            confidence = round(random.uniform(0.55, 0.98), 2)
            answer = random.choice(_MOCK_ANSWERS)
            citation = random.choice(_MOCK_CITATIONS)

            questions[idx]["generated_answer"] = answer
            questions[idx]["confidence"] = confidence
            questions[idx]["citation"] = citation
            questions[idx]["fill_status"] = "filled"
            questions[idx]["status"] = "filled"

            yield {
                "event": "progress",
                "data": json.dumps({
                    "index": idx,
                    "status": "filled",
                    "generated_answer": answer,
                    "confidence": confidence,
                    "citation": citation,
                }),
            }

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
    Generate and download the filled RFI as Excel (.xlsm/.xlsx) or CSV.
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
    ext = Path(session["file_name"]).suffix.lower()

    if ext == ".xlsm":
        media_type = "application/vnd.ms-excel.sheet.macroEnabled.12"
    else:
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return FileResponse(
        output_path,
        media_type=media_type,
        filename=f"{basename}_FILLED{ext}",
    )


@app.get("/api/download-csv/{session_id}")
async def download_csv(session_id: str):
    """
    Generate and download the filled RFI as CSV.
    """
    import csv
    import io

    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["status"] not in ("filled", "reviewed"):
        raise HTTPException(400, "Questions must be filled before download")

    questions = session["questions"]
    basename = Path(session["file_name"]).stem
    output_path = str(UPLOAD_DIR / f"{basename}_FILLED.csv")

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Sheet", "Row", "Question", "Answer", "Confidence", "Citation"])
        for q in questions:
            writer.writerow([
                q.get("sheet_name", ""),
                q.get("row", ""),
                q.get("question_text", ""),
                q.get("generated_answer", ""),
                f"{int((q.get('confidence') or 0) * 100)}%",
                q.get("citation", ""),
            ])

    return FileResponse(
        output_path,
        media_type="text/csv",
        filename=f"{basename}_FILLED.csv",
    )


@app.get("/api/download-pdf/{session_id}")
async def download_pdf(session_id: str):
    """
    Generate and download the filled RFI as PDF.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if session["status"] not in ("filled", "reviewed"):
        raise HTTPException(400, "Questions must be filled before download")

    output_path = _generate_pdf(
        session["questions"],
        session["file_name"],
        session["client_name"],
    )
    basename = Path(session["file_name"]).stem

    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=f"{basename}_FILLED.pdf",
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
