"""
tts.py
------
Speaking out loud, without freezing the video loop.

    say("Stopping.")  ->  [queue]  ->  worker thread  ->  speaker

Primary voice is Sarvam's streaming TTS (`bulbul`), which speaks Hindi and
Indian English properly. Audio is played as it arrives rather than after the
whole clip is generated, so the robot starts talking sooner.

pyttsx3 (the built-in Windows voice) stays as an offline fallback for when
there is no API key or no network. It cannot speak Hindi well, but it is
better than silence.

The public interface is unchanged from the original version, so the rest of
the app did not have to be rewritten:

    start()          begin the worker thread
    say(text)        queue something to speak, returns immediately
    is_speaking()    True while audio is playing (used to mute the mic)
    interrupt()      stop mid-sentence, for barge-in
    shutdown()
"""

import asyncio
import base64
import queue
import threading
import time

import numpy as np

import config

_say_queue = queue.Queue()
_worker = None
_stop_flag = threading.Event()
_speaking = threading.Event()
_interrupt = threading.Event()
_quiet_until = 0.0
_TAIL_SECONDS = 0.4

# Sample rate we ask Sarvam for. 22050 is its default and sounds fine.
_TTS_RATE = 22050

_sarvam_ok = None      # None = untried, True/False once we know
_last_error = None


def last_error():
    return _last_error


# --------------------------------------------------------------------------
# Sarvam streaming voice
# --------------------------------------------------------------------------

async def _speak_sarvam(text, language_code):
    """Stream audio from Sarvam and play it as the chunks arrive."""
    global _last_error
    import sounddevice as sd
    from sarvamai import AsyncSarvamAI

    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    stream = sd.OutputStream(samplerate=_TTS_RATE, channels=1, dtype="int16")
    stream.start()
    played_anything = False

    try:
        async with client.text_to_speech_streaming.connect(
                model=config.TTS_MODEL, send_completion_event="true") as ws:

            await ws.configure(
                target_language_code=language_code,
                speaker=config.TTS_SPEAKER,
                output_audio_codec="linear16",   # raw PCM, no decoder needed
                speech_sample_rate=_TTS_RATE,
            )
            await ws.convert(text)
            await ws.flush()

            while True:
                # Barge-in: drop everything and stop talking immediately.
                if _interrupt.is_set():
                    break

                message = await ws.recv()
                kind = getattr(message, "type", None)

                if kind == "audio":
                    raw = base64.b64decode(message.data.audio)
                    if raw:
                        samples = np.frombuffer(raw, dtype="<i2")
                        # write() blocks while the speaker drains, so run it
                        # off the event loop.
                        await asyncio.to_thread(stream.write, samples)
                        played_anything = True

                elif kind == "event":
                    break        # completion event - the sentence is done

                elif kind == "error":
                    _last_error = str(getattr(message.data, "message", message.data))
                    break
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    return played_anything


def _try_sarvam(text, language_code):
    """Returns True if Sarvam actually spoke."""
    global _last_error, _sarvam_ok
    if not config.SARVAM_API_KEY:
        _last_error = "SARVAM_API_KEY is not set"
        _sarvam_ok = False
        return False
    try:
        spoke = asyncio.run(_speak_sarvam(text, language_code))
        if spoke:
            _sarvam_ok = True
        return spoke
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        if _sarvam_ok is not False:
            print(f"[tts] Sarvam voice failed ({_last_error}).")
            print("[tts] Falling back to the offline Windows voice.")
        _sarvam_ok = False
        return False


# --------------------------------------------------------------------------
# Offline fallback voice
# --------------------------------------------------------------------------

def _make_offline_engine():
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        return engine
    except Exception as exc:
        print(f"[tts] Offline voice unavailable too: {exc}")
        return None


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------

def _run():
    global _quiet_until

    offline = None          # created lazily, only if we actually need it

    while not _stop_flag.is_set():
        try:
            item = _say_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if item is None:
            break

        text, language_code = item
        _interrupt.clear()
        _speaking.set()
        try:
            spoke = _try_sarvam(text, language_code)
            if not spoke and not _interrupt.is_set():
                if offline is None:
                    offline = _make_offline_engine()
                if offline is not None:
                    offline.say(text)
                    offline.runAndWait()
                else:
                    print(f"[robot would say] {text}")
                    time.sleep(0.5)
        except Exception as exc:
            print(f"[tts] Failed to speak {text!r}: {exc}")
        finally:
            _quiet_until = time.time() + _TAIL_SECONDS
            _speaking.clear()

    if offline is not None:
        try:
            offline.stop()
        except Exception:
            pass


def start(rate=None, voice_hint=None):
    """
    Start the speaking thread.

    rate and voice_hint are accepted for backwards compatibility with the
    original pyttsx3-only version; Sarvam uses config.TTS_SPEAKER instead.
    """
    global _worker
    if _worker is not None:
        return
    _stop_flag.clear()
    _worker = threading.Thread(target=_run, name="tts", daemon=True)
    _worker.start()


def say(text, language_code=None):
    """Queue a sentence. Returns immediately; it does not wait for audio."""
    if not text:
        return
    if language_code is None:
        # Pick Hindi if the text is Devanagari, otherwise Indian English.
        language_code = ("hi-IN" if any("ऀ" <= ch <= "ॿ" for ch in text)
                         else "en-IN")
    _say_queue.put((text, language_code))


# Alias, because the spec asks for speak().
speak = say


def is_speaking():
    """True while audio is playing, plus a short tail for the room echo."""
    return _speaking.is_set() or time.time() < _quiet_until


def interrupt():
    """
    Stop talking right now and drop anything queued.

    This is what makes barge-in work: when the wake word is heard while the
    robot is mid-sentence, we cut the audio and start listening.
    """
    _interrupt.set()
    while True:
        try:
            _say_queue.get_nowait()
        except queue.Empty:
            break


def shutdown():
    global _worker
    if _worker is None:
        return
    _interrupt.set()
    _stop_flag.set()
    _say_queue.put(None)
    _worker.join(timeout=5.0)
    _worker = None


# Quick check:  python tts.py
if __name__ == "__main__":
    start()
    print("Sarvam key:", "set" if config.SARVAM_API_KEY else "MISSING (will use offline voice)")
    say("Okay, following you.", "en-IN")
    say("ठीक है, मैं आपके साथ चल रहा हूँ।", "hi-IN")
    say("Stopping.", "en-IN")
    while is_speaking() or not _say_queue.empty():
        time.sleep(0.1)
    time.sleep(0.5)
    shutdown()
    print("last error:", last_error())
    print("done")
