# app/main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv


import os, pathlib
print(">>> Loaded main from:", os.path.abspath(__file__))
print(">>> CWD:", os.getcwd())

load_dotenv()

app = FastAPI(title="LockedInterview API", version="0.1.0")

# CORS (lock down in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Import routers AFTER app is created ----
from app.routers.session import router as session_router
from app.routers.behavioral import router as behavioral_router
from app.routers.diag import router as diag_router
# app/main.py (only the new lines shown)
from app.routers.speech import router as speech_router
app.include_router(speech_router, prefix="/speech", tags=["Speech"])


# ---- Include routers ----
app.include_router(session_router, prefix="/session", tags=["Session"])
app.include_router(behavioral_router, prefix="/score/behavioral", tags=["Behavioral"])
app.include_router(diag_router, prefix="/diag", tags=["Diag"])
@app.get("/diag/openrouter")
def openrouter_diag():
    import os
    return {
        "has_key": bool(os.environ.get("OPENROUTER_API_KEY", "")),
        "model": os.environ.get("OPENROUTER_MODEL"),
    }

@app.get("/healthz")
def healthz():
    return {"ok": True}
