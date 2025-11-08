# app/routers/behavioral.py
import os
import json
import re
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.openrouter import stream_chat_completion  # your existing streamer
from app.routers.session import get_session_prefs  # session-aware scoring context

router = APIRouter()

class ScoreReq(BaseModel):
    sessionId: str
    questionId: str
    answerText: str = Field(min_length=1)

def _build_behavioral_messages(answer: str, *, role: str, level: str, difficulty: str, tracks: list[str] | None):
    topics = ", ".join(tracks or [])
    context = (
        f"Candidate target role: {role}\n"
        f"Candidate seniority level: {level}\n"
        f"Intended difficulty: {difficulty}\n"
        f"Topic focus: {topics if topics else 'General'}\n"
    )

    rubric = """
You are a strict interview coach scoring a behavioral answer by the STAR method.

Scoring axes (0–10 each):
- Structure: Is there a clear Situation, Task, Action, Result? Are transitions crisp?
- Clarity: Is the narrative specific, concise, and technically intelligible for the target role/level?
- Impact: Are outcomes quantified or clearly evidenced? Ownership, leadership, and learning called out?

Adjustment by context:
- If the level is Senior, expect scope beyond self (team/org), proactive alignment, metrics, and trade-offs.
- If the level is Intern/Junior, focus on clarity, reflection, and concrete contributions.
- Align expectations with the stated difficulty and topics when judging specificity and depth.

Output JSON only (no prose), with this exact schema:
{
  "structure": 0-10,
  "clarity": 0-10,
  "impact": 0-10,
  "summary": "2-4 sentences of targeted feedback with specific, actionable suggestions"
}
""".strip()

    return [
        {"role": "system", "content": rubric},
        {"role": "user", "content": f"Context:\n{context}\n\nAnswer to score:\n{answer.strip()}"},
        # Nudge towards JSON
        {"role": "assistant", "content": '{"structure": '},
    ]

def _heuristic_star_fallback(answer: str, *, role: str, level: str, difficulty: str):
    """
    Local scoring when OpenRouter is unavailable.
    Very lightweight heuristic: looks for STAR cues, specificity, and impact signals.
    """
    text = answer.lower()

    # Structure: presence of STAR cues
    cues = sum(bool(re.search(rf"\b{w}\b", text)) for w in ["situation", "task", "action", "result"])
    structure = min(10, 3 + cues * 2)  # base 3, +2 per cue

    # Clarity: penalize very long sentences, filler; reward specifics (numbers/tech terms)
    sentences = max(1, text.count(".") + text.count("!") + text.count("?"))
    avg_len = len(text.split()) / sentences
    fillers = len(re.findall(r"\b(kinda|sort of|like|um|uh)\b", text))
    specifics = len(re.findall(r"\b\d+(\.\d+)?%?\b", text)) + len(re.findall(r"\b(sla|p95|latency|throughput|sql|api|cache|deploy)\b", text))
    clarity = max(0, min(10, 6 - (avg_len > 28) * 2 - min(fillers, 3) + min(specifics, 4)))

    # Impact: look for results/metrics/ownership verbs
    impact_terms = len(re.findall(r"\b(reduced|increased|shipped|launched|cut|grew|saved|improved)\b", text))
    metrics = len(re.findall(r"\b\d+(\.\d+)?%?\b", text))
    ownership = bool(re.search(r"\b(i led|i owned|i drove|i proposed)\b", text))
    impact = max(0, min(10, 4 + min(impact_terms, 3) + min(metrics, 3) + (2 if ownership else 0)))

    # Slight leveling effect
    if level.lower() in ("senior",):
        structure = min(10, structure + 1)
        impact = min(10, impact + 1)

    summary_bits = []
    if cues < 3: summary_bits.append("Tighten STAR framing (state Situation, Task, Action, Result).")
    if clarity < 6: summary_bits.append("Use concrete details; trim filler and long sentences.")
    if impact < 6: summary_bits.append("Quantify outcomes and highlight ownership.")
    if not summary_bits:
        summary_bits = ["Strong structure and signal; consider adding 1–2 crisp metrics to elevate impact."]

    return {
        "structure": int(structure),
        "clarity": int(clarity),
        "impact": int(impact),
        "summary": " ".join(summary_bits)[:500],
    }

@router.post("/stream")
async def score_behavioral_stream(req: ScoreReq):
    prefs = get_session_prefs(req.sessionId) or {}
    role = prefs.get("role", "SWE")
    level = prefs.get("level", "Intern")
    difficulty = prefs.get("difficulty", "medium")
    tracks = prefs.get("tracks") or None

    # If OpenRouter is configured, stream model deltas; else stream a local fallback once.
    has_openrouter = bool(os.getenv("OPENROUTER_API_KEY", "")) and bool(os.getenv("OPENROUTER_MODEL", ""))

    async def eventgen():
        if has_openrouter:
            try:
                messages = _build_behavioral_messages(
                    req.answerText, role=role, level=level, difficulty=difficulty, tracks=tracks
                )
                assembled_any = False
                async for chunk in stream_chat_completion(messages):
                    assembled_any = True
                    yield json.dumps({"delta": chunk}).encode("utf-8")
                    yield b"\n"
                if not assembled_any:
                    # Safety: if the streamer yielded nothing, fall back
                    result = _heuristic_star_fallback(req.answerText, role=role, level=level, difficulty=difficulty)
                    yield json.dumps({"delta": json.dumps(result)}).encode("utf-8"); yield b"\n"
                yield json.dumps({"done": True}).encode("utf-8")
            except Exception as e:
                # On error, send a deterministic fallback so the UI still renders feedback
                result = _heuristic_star_fallback(req.answerText, role=role, level=level, difficulty=difficulty)
                yield json.dumps({"delta": json.dumps(result)}).encode("utf-8"); yield b"\n"
                yield json.dumps({"error": str(e)}).encode("utf-8"); yield b"\n"
        else:
            # Offline deterministic scoring path
            result = _heuristic_star_fallback(req.answerText, role=role, level=level, difficulty=difficulty)
            yield json.dumps({"delta": json.dumps(result)}).encode("utf-8"); yield b"\n"
            yield json.dumps({"done": True}).encode("utf-8")

    return StreamingResponse(eventgen(), media_type="application/x-ndjson")
