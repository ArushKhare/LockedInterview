# app/routers/speech.py
import os
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

# Accepts audio/* (webm, wav, m4a, mp3). Uses OpenAI Whisper if OPENAI_API_KEY is set.
@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        raise HTTPException(status_code=501, detail="Speech-to-text not configured. Set OPENAI_API_KEY.")

    try:
        from openai import OpenAI
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"openai package missing: {e}")

    client = OpenAI(api_key=openai_key)

    # OpenAI supports webm/opus directly; we stream bytes through.
    # NOTE: For large files you may want to spool to disk.
    try:
        contents = await file.read()
        # Create a pseudo file object
        import io
        faux = io.BytesIO(contents)
        faux.name = file.filename or "audio.webm"

        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=faux,
            response_format="json",
            temperature=0
        )
        text = getattr(resp, "text", "") or (resp.get("text") if isinstance(resp, dict) else "")
        return JSONResponse({"text": text})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
