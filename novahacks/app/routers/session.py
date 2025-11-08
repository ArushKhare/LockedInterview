# app/routers/session.py
import os
import uuid
import json
import time
import hashlib
import random
import string
from collections import defaultdict, deque
from typing import Optional, Literal, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.openrouter import stream_chat_completion

router = APIRouter()

# --------------------------------------------------------------------------------------
# In-memory state
# --------------------------------------------------------------------------------------
SESSIONS: dict[str, dict] = {}  # session_id -> {prefs, queue:[questions], used_hashes:set, counter:int}

# --- Session store and simple getter used by the scorer ---
# Shape:
# SESSIONS[session_id] = {
#   "prefs": { "role": ..., "level": ..., "type": ..., ... },
#   "queue": [ ...5 questions... ],
#   "used_hashes": [...],
#   "counter": int,
# }
SESSIONS: dict[str, dict] = {}

def get_session_prefs(session_id: str) -> dict:
    """
    Returns the session preference dict the scorer needs, e.g.:
      { "role": "SWE", "level": "New Grad", "type": "behavioral", ... }
    If the session isn't found, returns a sensible default instead of crashing.
    """
    st = SESSIONS.get(session_id)
    if isinstance(st, dict):
        # if you stored prefs under st["prefs"] (batch generator version)
        prefs = st.get("prefs")
        if isinstance(prefs, dict):
            return prefs
        # older single-pass versions stored prefs directly as the session dict
        # fall back to that:
        return st
    # default fallback so scoring doesn't explode
    return {"role": "SWE", "level": "Intern", "type": "behavioral"}

# recent de-dup memory across sessions (per role+type)
RECENT_HASHES: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))  # key -> recent question hashes
RECENT_TOPICS: dict[str, deque] = defaultdict(lambda: deque(maxlen=150))  # key -> recent categories/tags

SERVER_SALT = uuid.uuid4().hex  # fixed while process runs, increases prompt entropy

# --------------------------------------------------------------------------------------
# Types / payload
# --------------------------------------------------------------------------------------
Role = Literal["SWE", "Data Analyst", "Consultant", "Product Manager"]
Level = Literal["Intern", "New Grad", "Intermediate", "Senior"]
InterviewType = Literal["behavioral", "technical"]

class StartSessionReq(BaseModel):
    role: Role = "SWE"
    level: Level = "Intern"
    type: InterviewType = "behavioral"
    totalQuestions: int = Field(default=5, ge=5, le=5)  # fixed to 5 by product

class Question(BaseModel):
    type: InterviewType
    text: str
    tags: List[str] = []
    metadata: dict = {}

# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------
def _norm_text(s: str) -> str:
    return "".join(ch for ch in s.lower().strip() if ch.isalnum() or ch.isspace())

def _qhash(text: str) -> str:
    return hashlib.sha1(_norm_text(text).encode("utf-8")).hexdigest()

def _recent_key(role: str, itype: str) -> str:
    return f"{role}::{itype}"

def _rnd_token(n=8) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))

def _has_openrouter() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "")) and bool(os.getenv("OPENROUTER_MODEL", ""))

# --------------------------------------------------------------------------------------
# Prompt builders
# --------------------------------------------------------------------------------------
_BEHAVIORAL_TOPICS = ["Leadership", "Ownership", "Conflict", "Communication", "Ambiguity", "Stakeholders", "Collaboration", "Delivery"]
_TECH_TOPICS = ["Algorithms", "Data Structures", "System Design", "Debugging", "Databases", "Concurrency", "Estimation", "Data Modeling"]

def _topic_plan(itype: str) -> List[str]:
    # choose 5 distinct categories per type
    base = _BEHAVIORAL_TOPICS if itype == "behavioral" else _TECH_TOPICS
    base = base[:]  # copy
    random.shuffle(base)
    return base[:5]

def _system_prompt_batch() -> str:
    return (
        "You are an expert interviewer generating 5 distinct interview questions.\n"
        "Requirements:\n"
        " - Questions MUST be on DIFFERENT categories/topics.\n"
        " - Tailor depth to the candidate's level and role.\n"
        " - For behavioral: STAR-friendly prompts (no solutions).\n"
        " - For technical: no solutions; concise, unambiguous phrasing.\n"
        " - Avoid repeating wording across items; vary scenario and verbs.\n"
        "OUTPUT: Return ONLY a JSON array of exactly 5 objects, each with schema:\n"
        "{ \"type\":\"behavioral|technical\", \"text\":\"<concise question>\", \"tags\":[\"topic\",\"role\"], "
        "\"metadata\":{\"level\":\"Intern|New Grad|Intermediate|Senior\",\"category\":\"<topic>\"} }\n"
        "No prose, no code fences, JSON array only."
    )


def _user_prompt_batch(*, role: str, level: str, itype: str,
                       avoid_categories: List[str], avoid_phrases: List[str],
                       planned_topics: List[str], noise: str) -> str:
    avoid_cats = ", ".join(avoid_categories) if avoid_categories else "None"
    avoid_phr = "; ".join(avoid_phrases[:10]) if avoid_phrases else "None"
    plan = ", ".join(planned_topics)
    return (
        f"Role: {role}\n"
        f"Level: {level}\n"
        f"Interview type: {itype}\n"
        f"Planned categories to cover (all must be distinct): {plan}\n"
        f"Avoid categories (do not reuse): {avoid_cats}\n"
        f"Avoid phrasing hints (do not echo): {avoid_phr}\n"
        f"Entropy: {noise}\n"
        "Return a JSON array of 5 distinct questions as per schema."
    )

def _system_prompt_single() -> str:
    return _system_prompt_batch().replace("5 distinct interview questions", "1 interview question")

def _user_prompt_single(*, role: str, level: str, itype: str,
                        avoid_categories: List[str], avoid_phrases: List[str],
                        planned_topic: str, noise: str) -> str:
    return _user_prompt_batch(
        role=role, level=level, itype=itype,
        avoid_categories=avoid_categories, avoid_phrases=avoid_phrases,
        planned_topics=[planned_topic], noise=noise
    )

# --------------------------------------------------------------------------------------
# Stream + parsing helpers
# --------------------------------------------------------------------------------------
def _extract_piece(chunk) -> str:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, bytes):
        try:
            return chunk.decode("utf-8", "ignore")
        except Exception:
            return ""
    if isinstance(chunk, dict):
        if "delta" in chunk and isinstance(chunk["delta"], str):
            return chunk["delta"]
        if "content" in chunk and isinstance(chunk["content"], str):
            return chunk["content"]
        try:
            choices = chunk.get("choices") or []
            if choices and isinstance(choices, list):
                delta = choices[0].get("delta") or {}
                piece = delta.get("content") or ""
                if isinstance(piece, str):
                    return piece
        except Exception:
            pass
    return ""

async def _stream_all(messages) -> str:
    buf = ""
    async for chunk in stream_chat_completion(messages):
        buf += _extract_piece(chunk)
    return buf

def _parse_json_array(s: str) -> Optional[List[dict]]:
    s = (s or "").strip()
    first, last = s.find("["), s.rfind("]")
    if first == -1 or last == -1 or last <= first:
        return None
    try:
        data = json.loads(s[first:last+1])
        if isinstance(data, list):
            return data
    except Exception:
        return None
    return None

def _parse_json_single(s: str) -> Optional[dict]:
    s = (s or "").strip()
    first, last = s.find("{"), s.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    try:
        data = json.loads(s[first:last+1])
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None

# --------------------------------------------------------------------------------------
# Fallback banks (distinct topics)
# --------------------------------------------------------------------------------------
FALLBACK_BEHAVIORAL = {
    "Intern": [
        ("Learning", "Tell me about a time you learned from a mistake on a project. What changed afterward?"),
        ("Conflict", "Describe a team conflict you witnessed. How did you contribute to resolving it?"),
        ("Ownership", "Tell me about a time you took ownership beyond your task to unblock the team."),
        ("Communication", "Describe a time you had to simplify a complex idea for a non-technical audience."),
        ("Ambiguity", "Give an example of tackling an ambiguous task. How did you clarify scope?")
    ],
    "New Grad": [
        ("Prioritization", "Tell me about a time you balanced competing deadlines. How did you prioritize and what was the result?"),
        ("Feedback", "Describe a time you received critical feedback. What did you change?"),
        ("Leadership", "Tell me about a time you led a small effort without formal authority."),
        ("Collaboration", "Describe when cross-team collaboration was required. How did you align goals?"),
        ("Delivery", "Give an example where you accelerated delivery without sacrificing quality.")
    ],
    "Intermediate": [
        ("Stakeholders", "Describe managing stakeholder expectations under changing requirements. What trade-offs did you make?"),
        ("Process", "Tell me about improving a process/system. How did you measure success?"),
        ("Conflict", "Explain a conflict across teams you resolved. What changed?"),
        ("Risk", "Describe how you de-risked a project under time pressure."),
        ("Mentorship", "Tell me about mentoring a teammate to unblock a deliverable.")
    ],
    "Senior": [
        ("Alignment", "Tell me about driving alignment across stakeholders with conflicting incentives."),
        ("Strategy", "Describe owning a high-ambiguity initiative end-to-end. How did you de-risk?"),
        ("Org Impact", "Give an example of raising the bar for quality at org level. How?"),
        ("Execution", "Describe rescuing a slipping project. What levers did you pull?"),
        ("Scaling", "Tell me about scaling a practice across teams. What outcomes improved?")
    ]
}

FALLBACK_TECH = {
    "Intern": [
        ("Data Structures", "Given an array and target k, return the length of the longest subarray that sums to k. Explain complexity."),
        ("Strings", "Check if a string is a permutation of a palindrome. Discuss time/space."),
        ("Sorting", "Explain stability in sorting algorithms and when it matters."),
        ("Hashing", "Design a simple LRU cache API and describe how you'd implement it."),
        ("Graphs", "Detect a cycle in a directed graph. Outline approaches.")
    ],
    "New Grad": [
        ("System Design", "Design a URL shortener: API, storage model, handling collisions, and scaling."),
        ("Concurrency", "Explain race conditions and how to prevent them in a shared counter."),
        ("Databases", "Model a basic ridesharing schema and key queries."),
        ("Monitoring", "What metrics and alerts would you add to an API service?"),
        ("Estimation", "Back-of-the-envelope: daily storage for 10M images with metadata.")
    ],
    "Intermediate": [
        ("Rate Limiting", "Design a rate limiter for an API gateway. Compare token vs leaky bucket."),
        ("Streaming", "Compute rolling p95 latency with limited memory over a log stream."),
        ("Caching", "Design a cache for hot keys across regions balancing latency and consistency."),
        ("Faults", "Design for graceful degradation when a downstream dependency is flaky."),
        ("Indexing", "Choose indexes for a high-write, read-latency-sensitive table.")
    ],
    "Senior": [
        ("Multi-Region", "Design a multi-region cache for hot keys; discuss failure modes and mitigation."),
        ("Tail Latency", "Your service’s p99 rose; propose experiments to reduce it without overprovisioning."),
        ("Capacity", "Create a capacity plan for a bursty workload with unpredictable spikes."),
        ("Backpressure", "Design backpressure to protect an overloaded service in a mesh."),
        ("Data Modeling", "Model an event-sourcing system for auditability and replay.")
    ]
}

def _fallback_batch(prefs: StartSessionReq) -> List[dict]:
    bank = FALLBACK_BEHAVIORAL if prefs.type == "behavioral" else FALLBACK_TECH
    items = bank.get(prefs.level, bank["New Grad"])[:]  # list of (category, text)
    random.shuffle(items)
    items = items[:5]
    out = []
    for cat, text in items:
        out.append({
            "type": prefs.type,
            "text": text,
            "tags": [prefs.type.capitalize(), prefs.role, cat],
            "metadata": {"level": prefs.level, "category": cat}
        })
    return out

# --------------------------------------------------------------------------------------
# LLM generation (batch of 5, then uniqueness filtering)
# --------------------------------------------------------------------------------------
async def _llm_batch_generate(prefs: StartSessionReq,
                              avoid_categories: List[str],
                              avoid_phrases: List[str],
                              planned_topics: List[str],
                              noise: str) -> Optional[List[dict]]:
    messages = [
        {"role": "system", "content": _system_prompt_batch()},
        {"role": "user", "content": _user_prompt_batch(
            role=prefs.role, level=prefs.level, itype=prefs.type,
            avoid_categories=avoid_categories, avoid_phrases=avoid_phrases,
            planned_topics=planned_topics, noise=noise
        )},
        {"role": "assistant", "content": '[{"type":"'},
    ]
    try:
        assembled = await _stream_all(messages)
        arr = _parse_json_array(assembled)
        if arr and len(arr) >= 5:
            return arr[:5]
        # one retry with stronger forcing
        messages2 = [
            {"role": "system", "content": _system_prompt_batch()},
            {"role": "user", "content": _user_prompt_batch(
                role=prefs.role, level=prefs.level, itype=prefs.type,
                avoid_categories=avoid_categories, avoid_phrases=avoid_phrases,
                planned_topics=planned_topics, noise=noise + "::retry"
            )},
            {"role": "assistant", "content": '[{"type":"'},
        ]
        assembled2 = await _stream_all(messages2)
        arr2 = _parse_json_array(assembled2)
        if arr2 and len(arr2) >= 5:
            return arr2[:5]
    except Exception:
        pass
    return None

def _distinct_filter(candidates: List[dict], used_hashes: set, key: str) -> List[dict]:
    """Drop duplicates by text hash and repeated categories, also avoid global recent."""
    distinct = []
    seen_cats = set()
    global_recent = set(RECENT_HASHES[key])
    for q in candidates:
        text = (q.get("text") or "").strip()
        if not text:
            continue
        h = _qhash(text)
        cat = ((q.get("metadata") or {}).get("category") or "").strip() or (q.get("tags") or [""])[0]
        if h in used_hashes or h in global_recent:
            continue
        if cat in seen_cats:
            continue
        seen_cats.add(cat)
        distinct.append(q)
        used_hashes.add(h)
    return distinct

def _record_recent(key: str, qs: List[dict]):
    for q in qs:
        text = (q.get("text") or "").strip()
        cat = ((q.get("metadata") or {}).get("category") or "").strip()
        if text:
            RECENT_HASHES[key].append(_qhash(text))
        if cat:
            RECENT_TOPICS[key].append(cat)

# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------
@router.post("/start")
async def start_session(req: StartSessionReq):
    """
    - Pre-generate a queue of 5 DISTINCT questions across different categories.
    - Store in session; /next-question will pop one at a time.
    """
    sid = str(uuid.uuid4())
    planned = _topic_plan(req.type)

    key = _recent_key(req.role, req.type)
    avoid_categories = list(set(list(RECENT_TOPICS[key])[-10:]))  # recent 10
    avoid_phrases = [h[:8] for h in list(RECENT_HASHES[key])[-10:]]  # short hashes as "phrasing hints"

    # Prompt noise to reduce accidental repeats across uses
    noise = f"salt={SERVER_SALT};ts={int(time.time())};sid={sid};ctr={random.randint(10_000,99_999)};token={_rnd_token(8)}"

    queue: List[dict] = []
    used_hashes: set[str] = set()

    if _has_openrouter():
        arr = await _llm_batch_generate(req, avoid_categories, avoid_phrases, planned, noise)
        if arr:
            queue = _distinct_filter(arr, used_hashes, key)

    # If LLM failed or not enough distinct, fill with fallback distinct set
    if len(queue) < 5:
        fb = _fallback_batch(req)
        fb = _distinct_filter(fb, used_hashes, key)
        # If still short (extreme edge), sample more from banks without repeat
        if len(queue) < 5:
            queue.extend([q for q in fb if q not in queue])
        queue = queue[:5]

    if len(queue) < 5:
        raise HTTPException(status_code=502, detail="Could not assemble 5 distinct questions.")

    # Persist session
    SESSIONS[sid] = {
        "prefs": req.model_dump(),
        "queue": queue,
        "used_hashes": list(used_hashes),
        "counter": 0,
    }

    # Record in global recent memory to avoid repeats in future sessions
    _record_recent(key, queue)

    return {
        "sessionId": sid,
        "questionCount": 5,
        "echo": req.model_dump(),
    }

@router.get("/{session_id}/next-question")
async def next_question(session_id: str):
    st = SESSIONS.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Unknown session")
    queue: List[dict] = st["queue"]
    prefs_raw = st["prefs"]
    prefs = StartSessionReq(**prefs_raw)
    counter = int(st.get("counter", 0))

    if counter >= len(queue):
        # Defensive: regenerate single unique question if user asks beyond 5
        key = _recent_key(prefs.role, prefs.type)
        used_hashes = set(st.get("used_hashes", []))
        avoid_categories = list(set(list(RECENT_TOPICS[key])[-10:]))
        avoid_phrases = [h[:8] for h in list(RECENT_HASHES[key])[-10:]]
        noise = f"salt={SERVER_SALT};ts={int(time.time())};sid={session_id};ctr={counter};token={_rnd_token(8)}"

        planned_topic = random.choice(_topic_plan(prefs.type))
        q = None
        if _has_openrouter():
            messages = [
                {"role": "system", "content": _system_prompt_single()},
                {"role": "user", "content": _user_prompt_single(
                    role=prefs.role, level=prefs.level, itype=prefs.type,
                    avoid_categories=avoid_categories, avoid_phrases=avoid_phrases,
                    planned_topic=planned_topic, noise=noise
                )},
                {"role": "assistant", "content": '{"type":"'},
            ]
            try:
                assembled = await _stream_all(messages)
                parsed = _parse_json_single(assembled)
                if isinstance(parsed, dict) and parsed.get("text"):
                    q = parsed
            except Exception:
                q = None

        if not q:
            # fallback single
            fb = _fallback_batch(prefs)
            # pick the first not-seen
            for cand in fb:
                if _qhash(cand["text"]) not in used_hashes:
                    q = cand
                    break
            if not q:
                q = fb[0]

        # final normalize
        q.setdefault("tags", [])
        q.setdefault("metadata", {})
        q["metadata"].setdefault("level", prefs.level)
        q["metadata"].setdefault("category", q["metadata"].get("category") or planned_topic)

        # append and update dedupe memory
        queue.append(q)
        used_hashes.add(_qhash(q["text"]))
        st["used_hashes"] = list(used_hashes)
        _record_recent(key, [q])

    # Pop next
    q = queue[counter]
    st["counter"] = counter + 1

    # Normalize output
    qtype = q.get("type", prefs.type)
    text = (q.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Empty question text.")
    tags = q.get("tags") or []
    metadata = q.get("metadata") or {}
    metadata.setdefault("level", prefs.level)
    metadata.setdefault("category", metadata.get("category") or "General")

    return {
        "questionId": str(uuid.uuid4()),
        "type": qtype,
        "text": text,
        "tags": tags,
        "metadata": metadata,
    }
