"""
stt.py
------
Speech to text using Sarvam's realtime WebSocket API.

Why this replaced the old local Whisper code:

* Sarvam's server does its own voice-activity detection and tells us when you
  stopped talking (`vad.speech_end`). The old code guessed that from a
  hand-tuned loudness threshold, which broke the moment the room was noisy.
* `mode="codemix"` and `language_code="auto"` handle Indian English, Hindi and
  mixed Hinglish in one stream, which local Whisper `tiny.en` could not.

We open a connection per utterance rather than holding one open forever. That
costs a few hundred milliseconds but keeps the code readable, and there is
nothing to leak if the network drops.

Audio arrives from audio_in, which is already buffering by the time we
connect, so nothing you say gets lost during connection setup.
"""

import asyncio
import base64
import threading

import numpy as np

import audio_in
import config

# ~100 ms of 16 kHz mono int16 = 1600 samples = 3200 bytes, the chunk size
# Sarvam's docs recommend.
CHUNK_SAMPLES = 1600

_last_error = None
_lock = threading.Lock()   # only one utterance at a time


def is_configured():
    """False if there is no API key, so callers can degrade gracefully."""
    return bool(config.SARVAM_API_KEY)


def last_error():
    return _last_error


def _event_name(message):
    """
    Get the event name off a Sarvam message.

    The SDK returns typed objects. Most carry an `event` field, but we fall
    back to the class name so an SDK change can't crash the robot.
    """
    name = getattr(message, "event", None)
    if isinstance(name, str):
        return name
    cls = type(message).__name__          # e.g. RealtimeTranscriptFinal
    return {
        "RealtimeSessionBegin": "session.begin",
        "RealtimeVadSpeechStart": "vad.speech_start",
        "RealtimeVadSpeechEnd": "vad.speech_end",
        "RealtimeTranscriptPartial": "transcript.partial",
        "RealtimeTranscriptFinal": "transcript.final",
        "RealtimeSessionEnd": "session.end",
        "RealtimeError": "error",
    }.get(cls, cls)


async def _stream_once(timeout, on_partial, on_speech_start):
    """Open a session, stream mic audio, return the final transcript."""
    from sarvamai import AsyncSarvamAI, RealtimeAudioInput, RealtimeEnd

    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    result = {"text": None, "error": None}
    finished = asyncio.Event()

    async with client.speech_to_text_realtime_streaming.connect(
        language_code=config.STT_LANGUAGE,
        model="saaras:v3-realtime",
        stream_type="fast",
        mode=config.STT_MODE,
        endpointing="vad",              # let the server decide when you stop
        encoding="linear16",
        sample_rate="16000",
        silence_duration_ms=str(config.STT_SILENCE_MS),
    ) as ws:

        async def sender():
            """Pump microphone audio up to Sarvam until we're told to stop."""
            pending = []
            held = 0
            while not finished.is_set():
                # read_captured blocks briefly, so keep it off the event loop
                block = await asyncio.to_thread(audio_in.read_captured, 0.05)
                if block is None:
                    continue
                pending.append(block)
                held += len(block)
                if held >= CHUNK_SAMPLES:
                    chunk = np.concatenate(pending)
                    pending, held = [], 0
                    audio_b64 = base64.b64encode(chunk.astype("<i2").tobytes()).decode()
                    try:
                        await ws.send_realtime_audio_input(
                            RealtimeAudioInput(audio=audio_b64))
                    except Exception as exc:
                        result["error"] = f"send failed: {exc}"
                        finished.set()
                        return
            try:
                await ws.send_realtime_end(RealtimeEnd())
            except Exception:
                pass

        async def receiver():
            """Read events until we have a final transcript."""
            heard_speech = False
            while not finished.is_set():
                message = await ws.recv()
                event = _event_name(message)

                if event == "vad.speech_start":
                    heard_speech = True
                    if on_speech_start:
                        on_speech_start()

                elif event == "transcript.partial":
                    text = getattr(message, "text", "") or ""
                    if text and on_partial:
                        on_partial(text)

                elif event == "transcript.final":
                    text = (getattr(message, "text", "") or "").strip()
                    if text:
                        result["text"] = text
                        finished.set()
                        return
                    # An empty final after real speech means it heard nothing
                    # useful; keep waiting until the overall timeout.

                elif event == "vad.speech_end":
                    # Final transcript normally follows within a moment.
                    pass

                elif event == "error":
                    result["error"] = getattr(message, "message", "unknown error")
                    if getattr(message, "is_fatal", True):
                        finished.set()
                        return

                elif event == "session.end":
                    finished.set()
                    return

            _ = heard_speech

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        try:
            await asyncio.wait_for(finished.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            result["error"] = result["error"] or "timed out waiting for speech"
        finally:
            finished.set()
            for task in (send_task, recv_task):
                task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)

    return result


def listen_once(timeout=None, on_partial=None, on_speech_start=None):
    """
    Capture one spoken utterance and return the text, or None.

    Blocking - call it from the voice thread, never from the camera loop.
    Never raises: on any failure it returns None and sets last_error().
    """
    global _last_error
    _last_error = None

    if not is_configured():
        _last_error = "SARVAM_API_KEY is not set"
        return None

    timeout = timeout if timeout is not None else config.LISTEN_TIMEOUT_S

    if not _lock.acquire(blocking=False):
        _last_error = "already listening"
        return None
    try:
        result = asyncio.run(_stream_once(timeout, on_partial, on_speech_start))
        _last_error = result.get("error")
        return result.get("text")
    except Exception as exc:
        # Network down, bad key, SDK change - all land here.
        _last_error = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        _lock.release()


# Standalone test:  python stt.py
if __name__ == "__main__":
    if not is_configured():
        raise SystemExit("SARVAM_API_KEY is not set - see .env.example")

    if not audio_in.start(blocksize=512):
        raise SystemExit(f"microphone unavailable: {audio_in.last_error()}")

    print("Speak in English or Hindi. 3 attempts.\n")
    for attempt in range(1, 4):
        print(f"--- attempt {attempt}: speak now ---")
        audio_in.arm_capture()
        text = listen_once(
            on_partial=lambda t: print(f"    ...{t}", end="\r", flush=True),
            on_speech_start=lambda: print("    [speech detected]"),
        )
        audio_in.disarm_capture()
        print(f"\n    FINAL: {text!r}   error={last_error()}")

        import commands
        print(f"    command: {commands.match_command(text or '')}\n")

    audio_in.stop()
