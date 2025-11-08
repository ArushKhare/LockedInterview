# app/routers/diag.py
import os
from fastapi import APIRouter

router = APIRouter()

@router.get("/openrouter")
def openrouter_diag():
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return {
        "has_key": bool(key),
        "model": os.environ.get("OPENROUTER_MODEL", None),
    }
