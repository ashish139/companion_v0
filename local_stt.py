"""
local_stt.py
------------
Offline English speech recognition. No API key, no network.

    mic --> cheap loudness gate --> Silero VAD --> Whisper --> text

Why two stages of detection?

The first version of this project used only a loudness threshold, and it was
the single most fragile thing in the codebase. If the room was noisy while it
calibrated, the threshold ended up above the user's speaking voice and the
microphone went deaf for the whole session while looking perfectly healthy.

So now:

* The loudness gate is FIXED and deliberately LOW. It never calibrates, so it
  can never be poisoned by background noise. Its only job is "might something
  be happening?", cheaply, on every 32 ms block.
* Silero VAD (a real neural voice-activity detector, already bundled with
  faster-whisper) then decides whether that audio actually contains speech,
  and trims it to just the speech. Fans, keyboard clicks and door bumps get
  thrown away here.
* Only what survives both goes to Whisper.

The result: a low gate we can afford precisely because something smarter sits
behind it.
"""

import time

import numpy as np

import audio_in
import config

# Whisper has never heard the robot's name, so left alone it guesses wildly:
# "Golu" came out as "Hello", "Galoo", "Galu", "Go look" and "Galo" depending
# on how it was said. `initial_prompt` is Whisper's built-in way to bias its
# vocabulary - we hand it a sentence containing the name and the commands, and
# it becomes far more likely to spell them the way we expect.
_PROMPT = (f"{config.WAKE_WORD}, follow me. "
           f"{config.WAKE_WORD}, stop. "
           f"Hey {config.WAKE_WORD}.")

SAMPLE_RATE = 16000

# Fixed, low, never adaptive. Real speech at this mic measures 0.02-0.20;
# an idle room measures about 0.00005.
GATE_RMS = 0.004

SILENCE_TO_END = 0.7      # seconds of quiet that end an utterance
MIN_UTTERANCE = 0.30      # ignore anything shorter
MAX_UTTERANCE = 8.0       # hard cap so one long noise can't run forever

_model = None
_vad_model = None
_last_error = None
_debug = False


def init(model_size="tiny.en", debug=False):
    """Load Whisper and the VAD. Returns True on success."""
    global _model, _vad_model, _last_error, _debug
    _debug = debug

    if _model is not None:
        return True

    try:
        from faster_whisper import WhisperModel
        print(f"[local-stt] Loading Whisper '{model_size}' (offline)...")
        _model = WhisperModel(model_size, device="cpu", compute_type="int8",
                              cpu_threads=4)
    except Exception as exc:
        _last_error = f"could not load Whisper: {exc}"
        print(f"[local-stt] {_last_error}")
        return False

    try:
        from faster_whisper.vad import get_vad_model
        _vad_model = get_vad_model()
    except Exception as exc:
        # Not fatal - we can still run on the loudness gate alone, just with
        # more false transcriptions.
        print(f"[local-stt] Silero VAD unavailable ({exc}); "
              f"falling back to the loudness gate only.")
        _vad_model = None

    print("[local-stt] Ready.")
    return True


def last_error():
    return _last_error


def _contains_speech(audio):
    """
    Ask Silero whether this clip really contains speech, and trim it.

    Returns the trimmed audio, or None if it is not speech.
    """
    if _vad_model is None:
        return audio          # no VAD available, trust the gate

    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        options = VadOptions(
            threshold=0.5,
            min_speech_duration_ms=200,
            min_silence_duration_ms=300,
            speech_pad_ms=200,
        )
        spans = get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE)
    except Exception as exc:
        if _debug:
            print(f"[local-stt] VAD error, passing audio through: {exc}")
        return audio

    if not spans:
        return None

    start = spans[0]["start"]
    end = spans[-1]["end"]
    return audio[start:end]


def looks_degenerate(text):
    """
    True if Whisper fell into a repetition loop.

    Fed borderline audio it sometimes emits the same phrase over and over -
    a real capture here produced "Good luck." seventy-five times. That is
    never a command, and acting on it makes the robot apologise at noise, so
    we throw the whole transcript away.
    """
    words = text.split()
    if len(words) < 8:
        return False
    for size in (1, 2, 3):
        first = tuple(words[:size])
        repeats = sum(1 for i in range(0, len(words) - size + 1, size)
                      if tuple(words[i:i + size]) == first)
        if repeats >= 4 and repeats * size > len(words) * 0.6:
            return True
    return False


def transcribe(speech):
    """
    Run Whisper on a clip that has already been trimmed to speech.

    The live loop and the tests both go through here on purpose. An earlier
    version had the tests call the model directly with their own arguments,
    which meant they silently skipped the initial_prompt and reported failures
    the real app did not have.
    """
    segments, _info = _model.transcribe(
        speech, language="en", beam_size=1, temperature=0.0,
        condition_on_previous_text=False, initial_prompt=_PROMPT,
    )
    return " ".join(s.text for s in segments).strip()


def next_utterance(timeout=None):
    """
    Wait for one spoken utterance and return its text, or None.

    Blocking. Call it from the voice thread, never the camera loop.
    Returns None on silence, on non-speech noise, or on timeout.
    """
    deadline = (time.time() + timeout) if timeout else None

    collected = []
    quiet_blocks = 0
    speaking = False
    block_seconds = None

    while True:
        if deadline and time.time() > deadline and not speaking:
            return None

        block = audio_in.read_captured(timeout=0.1)
        if block is None:
            continue

        if block_seconds is None:
            block_seconds = len(block) / SAMPLE_RATE

        samples = block.astype(np.float32) / 32768.0
        level = float(np.sqrt(np.mean(samples ** 2)))
        loud = level > GATE_RMS

        if not speaking:
            if loud:
                speaking = True
                collected = [samples]
                quiet_blocks = 0
            continue

        collected.append(samples)
        quiet_blocks = 0 if loud else quiet_blocks + 1

        long_pause = quiet_blocks * block_seconds >= SILENCE_TO_END
        too_long = len(collected) * block_seconds >= MAX_UTTERANCE
        if not (long_pause or too_long):
            continue

        # --- utterance finished ---
        audio = np.concatenate(collected)
        collected, speaking = [], False

        if len(audio) / SAMPLE_RATE < MIN_UTTERANCE:
            continue

        speech = _contains_speech(audio)
        if speech is None:
            if _debug:
                print(f"[local-stt] discarded {len(audio)/SAMPLE_RATE:.1f}s "
                      f"of non-speech noise")
            continue

        try:
            text = transcribe(speech)
        except Exception as exc:
            print(f"[local-stt] transcription failed: {exc}")
            continue

        if looks_degenerate(text):
            if _debug:
                print(f"[local-stt] discarded a repetition loop: "
                      f"{text[:60]!r}...")
            continue

        if _debug:
            print(f"[local-stt] heard: {text!r}")
        if text:
            return text


# Standalone test:  python local_stt.py
if __name__ == "__main__":
    import commands

    if not init(debug=True):
        raise SystemExit("could not start")
    if not audio_in.start(blocksize=512):
        raise SystemExit(f"microphone unavailable: {audio_in.last_error()}")

    audio_in.arm_capture()
    print("\nSpeak. Try 'Golu, follow me' and 'stop'. Ctrl+C to quit.\n")
    try:
        while True:
            text = next_utterance()
            if text:
                print(f"  -> {text!r}   command={commands.match_command(text)}")
    except KeyboardInterrupt:
        pass
    audio_in.stop()
