import os
import json
import modal
import requests

app = modal.App("text-analyzer")

image = (
    modal.Image.debian_slim()
    .apt_install("ffmpeg")
    .pip_install("pydub", "requests")
)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

format = """
    SCORE: ______
    FEEDBACK: _______
"""
format_statement = """Additionally, generate a score from 1-10. Just return the number. Put this score at the very end.
    Be a moderately difficult grader. NO MARKDOWN PLEASE. Keep to 500 tokens max. Strictly follow this format: {format}. In
    the score section, write it as "SCORE = <score>" and in the feedback section, write it as "FEEDBACK = <feedback>". DO NOT DEVIATE FROM FORMAT."""

@app.function(image=image, secrets=[modal.Secret.from_name("openrouter")], gpu="any")
def analyze_confidence(text):
    prompt = f"""
    Analyze the confidence of the inputted text: "{text}". 
    Consider the word choice of the user and the use of filler words. """ + format_statement

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "model": "anthropic/claude-sonnet-4.5",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1000
        })
    )
    response.raise_for_status()
    confidence_analysis = response.json()["choices"][0]["message"]["content"]

    return {
        "response": response,
        "confidence_analysis": confidence_analysis
    }

@app.function(image=image, secrets=[modal.Secret.from_name("openrouter")], gpu="any")
def analyze_word_choice(text):
    prompt = f"""
    Analyze the word choice of the inputted text: "{text}". 
    Consider how professional the wording is. """ + format_statement

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "model": "anthropic/claude-sonnet-4.5",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1000
        })
    )
    response.raise_for_status()
    word_analysis = response.json()["choices"][0]["message"]["content"]

    return {
        "response": response,
        "word_analysis": word_analysis
    }

@app.function(image=image, secrets=[modal.Secret.from_name("openrouter")], gpu="any")
def analyze_tone(text):
    prompt = f"""
    Analyze the tone of the inputted text: "{text}". 
    Consider the attitude conveyed by the tone of the text. """ + format_statement

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "model": "anthropic/claude-sonnet-4.5",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1000
        })
    )
    response.raise_for_status()
    tone_analysis = response.json()["choices"][0]["message"]["content"]

    return {
        "response": response,
        "tone_analysis": tone_analysis
    }

@app.local_entrypoint()
def main():
    text = "It might be best to hire me."
    result_confidence = analyze_confidence.remote(text)
    result_word = analyze_word_choice.remote(text)
    result_tone = analyze_tone.remote(text)

    print("\n*** --- CONFIDENCE ANALYSIS --- ***\n")
    print(result_confidence["confidence_analysis"])
    print("\n*** --- WORD CHOICE ANALYSIS --- ***\n")
    print(result_word["word_analysis"])
    print("\n*** --- TONE ANALYSIS --- ***\n")
    print(result_tone["tone_analysis"])