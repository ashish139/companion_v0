"""
voice.py
--------
The conversation loop, on its own thread.

    SLEEPING --"Golu"--> LISTENING --you stop talking--> PROCESSING
        ^                                                     |
        +------------- reply finishes <---- SPEAKING <--------+

The camera loop in main.py never waits for any of this. It reads
voice.state() for a label to print, and gets a callback when a command is
recognised.

There are two backends, chosen automatically at startup:

  LOCAL   (no keys needed, English only)
          Everything runs on this laptop. Silero VAD finds your speech,
          Whisper transcribes it, and the robot's name is spotted in the
          transcript. Because the name and the command arrive in the same
          transcript, you can say "Golu, follow me" in one breath.

  SARVAM  (needs PICOVOICE_ACCESS_KEY and SARVAM_API_KEY)
          Porcupine listens for the name on-device, then Sarvam's realtime
          API transcribes. Adds Hindi and Hinglish, and supports barge-in.

Self-triggering is prevented in both: the microphone capture is disarmed
while the robot is speaking, so it can never transcribe its own voice.
"""

import threading
import time

import audio_in
import commands
import config
import local_stt
import stt
import tts
import wakeword

SLEEPING = "SLEEPING"
LISTENING = "LISTENING"
PROCESSING = "PROCESSING"
SPEAKING = "SPEAKING"

# How long the robot stays awake waiting for a command after just its name.
AWAKE_SECONDS = 8.0

# Longest an utterance can be and still plausibly be a command. Anything
# longer, said without the robot's name, is treated as overheard conversation
# and ignored in silence.
MAX_COMMAND_WORDS = 10

_state = SLEEPING
_state_lock = threading.Lock()
_last_heard = None

_wake_event = threading.Event()
_stop_flag = threading.Event()
_thread = None
_on_command = None
_backend = None

_NOT_UNDERSTOOD = "Sorry, I didn't catch that."


def state():
    with _state_lock:
        return _state


def backend():
    return _backend


def last_heard():
    return _last_heard


def _set_state(new_state):
    global _state
    with _state_lock:
        _state = new_state


def _speak_and_wait(text, language_code="en-IN"):
    """
    Say something, with the microphone deafened for the duration.

    Disarming capture is what stops the robot hearing its own reply and
    treating it as a command. We drain whatever leaked into the buffer before
    re-arming, so the next utterance starts clean.
    """
    _set_state(SPEAKING)
    audio_in.disarm_capture()
    tts.say(text, language_code)

    time.sleep(0.3)                      # let the queue pick it up
    deadline = time.time() + 25
    while time.time() < deadline:
        if _stop_flag.is_set() or _wake_event.is_set():
            break
        if not tts.is_speaking():
            break
        time.sleep(0.05)

    audio_in.drain_capture()
    audio_in.arm_capture()


def _dispatch(text):
    """
    Understand one utterance and reply. Returns True if it was a command.

    Shared by both backends so the behaviour is identical either way.
    """
    global _last_heard
    _last_heard = text

    _set_state(PROCESSING)
    command = commands.match_command(text)

    if command is not None:
        if _on_command:
            _on_command(command, text)
        _speak_and_wait(commands.reply_for(command, text),
                        commands.reply_language(text))
        return True

    _speak_and_wait(_NOT_UNDERSTOOD, commands.reply_language(text))
    return False


# --------------------------------------------------------------------------
# LOCAL backend - offline, English, no keys
# --------------------------------------------------------------------------

def _run_local():
    """
    Transcribe every utterance locally and act only on ones addressed to us.

    Whisper only runs when Silero VAD says there was actually speech, so this
    is not "Whisper running continuously" - an idle room costs nothing.
    """
    audio_in.arm_capture()
    awake_until = 0.0

    while not _stop_flag.is_set():
        awake = time.time() < awake_until
        _set_state(LISTENING if awake else SLEEPING)

        text = local_stt.next_utterance(timeout=0.5)
        if text is None:
            continue
        if _stop_flag.is_set():
            break

        heard_name, remainder = commands.split_wake_word(text)

        # Ignore ordinary conversation: unless the robot was called, or is
        # already awake from a moment ago, nothing said is meant for it.
        if not heard_name and not (time.time() < awake_until):
            continue

        spoken = remainder if heard_name else text

        if heard_name and not spoken.strip():
            # Just the name on its own - acknowledge and wait for the command.
            _speak_and_wait("Yes?")
            awake_until = time.time() + AWAKE_SECONDS
            continue

        # A long sentence is somebody talking, not somebody giving an order.
        # Without this the robot apologises at every overheard conversation
        # for as long as it happens to be awake.
        if not heard_name and len(spoken.split()) > MAX_COMMAND_WORDS:
            continue

        acted = _dispatch(spoken)
        # After a command we go back to sleep; after a misunderstanding we
        # stay awake briefly so you can simply repeat yourself.
        awake_until = 0.0 if acted else time.time() + AWAKE_SECONDS

    audio_in.disarm_capture()


# --------------------------------------------------------------------------
# SARVAM backend - wake word on-device, recognition in the cloud
# --------------------------------------------------------------------------

def _on_wake():
    """Called from the audio thread the instant Porcupine fires. Must be fast."""
    tts.interrupt()          # barge-in: stop mid-sentence if talking
    _wake_event.set()


def _run_sarvam():
    while not _stop_flag.is_set():
        _set_state(SLEEPING)
        if not _wake_event.wait(timeout=0.2):
            continue
        _wake_event.clear()

        _set_state(LISTENING)
        audio_in.arm_capture()
        try:
            text = stt.listen_once()
        finally:
            audio_in.disarm_capture()

        if not text:
            err = stt.last_error()
            if err and "timed out" not in err:
                print(f"[voice] speech recognition unavailable: {err}")
            continue

        _dispatch(text)


def _run():
    try:
        if _backend == "local":
            _run_local()
        else:
            _run_sarvam()
    except Exception as exc:
        print(f"[voice] voice loop stopped: {type(exc).__name__}: {exc}")
    finally:
        _set_state(SLEEPING)


# --------------------------------------------------------------------------

def start(on_command, prefer=None, whisper_model="tiny.en", debug=False):
    """
    Start the voice loop. Returns True if it is usable.

    Picks the Sarvam backend when both keys are present, otherwise falls back
    to the fully local one. Pass prefer="local" or "sarvam" to force it.
    """
    global _thread, _on_command, _backend

    _on_command = on_command

    if not audio_in.is_available():
        print("[voice] No microphone, so voice control is off.")
        return False

    can_sarvam = bool(config.PICOVOICE_ACCESS_KEY) and stt.is_configured()
    choice = prefer or ("sarvam" if can_sarvam else "local")

    if choice == "sarvam":
        if not can_sarvam:
            print("[voice] Sarvam backend needs both PICOVOICE_ACCESS_KEY and"
                  " SARVAM_API_KEY.")
            return False
        if not wakeword.init():
            return False
        wakeword.listen(_on_wake)
        _backend = "sarvam"
        print("[voice] Backend: Sarvam (Hindi + English, barge-in enabled).")
    else:
        if not local_stt.init(model_size=whisper_model, debug=debug):
            print("[voice] Local speech recognition unavailable.")
            return False
        _backend = "local"
        print("[voice] Backend: local Whisper (English only, offline).")
        if not can_sarvam:
            print("        Add PICOVOICE_ACCESS_KEY and SARVAM_API_KEY to .env"
                  " for Hindi and barge-in.")

    _stop_flag.clear()
    _thread = threading.Thread(target=_run, name="voice", daemon=True)
    _thread.start()
    return True


def is_enabled():
    return _thread is not None


def stop():
    global _thread
    if _thread is None:
        return
    _stop_flag.set()
    _wake_event.set()
    _thread.join(timeout=5.0)
    _thread = None
