"""
config.py
---------
One place for settings and secrets.

Reads a .env file if one exists, then falls back to real environment
variables. Nothing here is ever committed - see .env.example for the shape
of the file you need to create.

Import this early, before anything that needs a key.
"""

import os

# python-dotenv is optional. If it isn't installed we simply use whatever is
# already in the real environment, so the app still starts.
try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env from the current directory if present
except ImportError:
    pass


def _get(name, default=None):
    """Read an env var, treating blank strings as missing."""
    value = os.environ.get(name, "")
    value = value.strip() if value else ""
    return value if value else default


# --- secrets (never hard-code these) ---------------------------------------
PICOVOICE_ACCESS_KEY = _get("PICOVOICE_ACCESS_KEY")
SARVAM_API_KEY = _get("SARVAM_API_KEY")

# --- wake word --------------------------------------------------------------
# The name you say to wake the robot. "Milo" is NOT one of Porcupine's
# built-in keywords, so to actually use it you must train a custom keyword
# (free) at https://console.picovoice.ai, download the Windows .ppn file, and
# point WAKE_WORD_PPN at it.
WAKE_WORD = _get("WAKE_WORD", "Milo")

# Path to a custom .ppn keyword file. If unset we fall back to a built-in.
WAKE_WORD_PPN = _get("WAKE_WORD_PPN")

# Used only when no .ppn is supplied, so the app is runnable before you train
# a custom word. Must be one of Porcupine's built-in keywords.
WAKE_WORD_FALLBACK = _get("WAKE_WORD_FALLBACK", "computer")

# How eager the wake-word detector is, 0..1. Higher = more detections and
# more false alarms.
WAKE_SENSITIVITY = float(_get("WAKE_SENSITIVITY", "0.6"))

# --- speech to text ---------------------------------------------------------
# "auto" lets Sarvam detect Hindi vs Indian English per utterance, which is
# what makes mixed Hindi/English work without the user switching a setting.
STT_LANGUAGE = _get("STT_LANGUAGE", "auto")

# "codemix" keeps Hinglish as it was spoken instead of forcing one script.
STT_MODE = _get("STT_MODE", "codemix")

# How long a pause means "they finished talking", in milliseconds.
STT_SILENCE_MS = int(_get("STT_SILENCE_MS", "600"))

# Give up on a single utterance after this long, so a stuck stream can't
# leave the robot listening forever.
LISTEN_TIMEOUT_S = float(_get("LISTEN_TIMEOUT_S", "8.0"))

# --- text to speech ---------------------------------------------------------
TTS_SPEAKER = _get("TTS_SPEAKER", "shubh")
TTS_MODEL = _get("TTS_MODEL", "bulbul:v3")

# --- audio ------------------------------------------------------------------
SAMPLE_RATE = 16000        # Porcupine and Sarvam both want 16 kHz mono
MIC_DEVICE = _get("MIC_DEVICE")
MIC_DEVICE = int(MIC_DEVICE) if MIC_DEVICE else None


def describe():
    """A one-line-per-setting summary for startup, with secrets masked."""
    def mask(v):
        return "set" if v else "MISSING"

    return [
        f"PICOVOICE_ACCESS_KEY : {mask(PICOVOICE_ACCESS_KEY)}",
        f"SARVAM_API_KEY       : {mask(SARVAM_API_KEY)}",
        f"wake word            : {WAKE_WORD}"
        + (f" (custom {os.path.basename(WAKE_WORD_PPN)})" if WAKE_WORD_PPN
           else f" -> no .ppn, falling back to built-in '{WAKE_WORD_FALLBACK}'"),
        f"STT                  : sarvam saaras:v3-realtime, lang={STT_LANGUAGE}, mode={STT_MODE}",
        f"TTS                  : sarvam {TTS_MODEL}, speaker={TTS_SPEAKER}",
    ]
