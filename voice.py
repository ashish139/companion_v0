"""
voice.py
--------
The conversation loop, on its own thread.

    SLEEPING ──"Milo"──► LISTENING ──you stop talking──► PROCESSING
        ▲                                                     │
        └──────────── reply finishes ◄──── SPEAKING ◄─────────┘

The camera loop in main.py never waits for any of this. It just asks
voice.state() for a label to print, and gets a callback when a command is
recognised.

Two things worth understanding:

Barge-in. The wake-word detector is deliberately left running while the robot
is speaking. Saying "Milo" mid-sentence cuts the audio and starts listening
again. This works precisely because the robot never says its own name, so it
cannot interrupt itself. Interrupting on *any* speech would need acoustic echo
cancellation, which we do not have - see the README.

Self-triggering. The speech recogniser is only fed audio in the LISTENING
state. While the robot is talking, nothing is being transcribed, so the robot
cannot hear its own reply and treat it as a command.
"""

import threading
import time

import audio_in
import commands
import config
import stt
import tts
import wakeword

SLEEPING = "SLEEPING"
LISTENING = "LISTENING"
PROCESSING = "PROCESSING"
SPEAKING = "SPEAKING"

_state = SLEEPING
_state_lock = threading.Lock()
_last_heard = None
_last_partial = None

_wake_event = threading.Event()
_stop_flag = threading.Event()
_thread = None
_on_command = None
_enabled = False

# What the robot says when it heard words but none of them were a command.
_NOT_UNDERSTOOD_EN = "Sorry, I didn't catch that."
_NOT_UNDERSTOOD_HI = "माफ़ कीजिए, समझ नहीं आया।"


def state():
    with _state_lock:
        return _state


def last_heard():
    return _last_heard


def last_partial():
    return _last_partial


def _set_state(new_state):
    global _state
    with _state_lock:
        _state = new_state


def _on_wake():
    """
    Called from the audio thread the instant the wake word is heard.
    Must return fast - no network, no blocking.
    """
    tts.interrupt()          # barge-in: stop mid-sentence if talking
    _wake_event.set()


def _handle_one_turn():
    """Wake word already heard. Listen, understand, reply."""
    global _last_heard, _last_partial

    # --- LISTENING ---------------------------------------------------
    _set_state(LISTENING)
    _last_partial = None
    audio_in.arm_capture()
    try:
        text = stt.listen_once(
            on_partial=_remember_partial,
            on_speech_start=None,
        )
    finally:
        audio_in.disarm_capture()

    if text is None:
        # Timed out, or STT is unavailable. Say nothing and go back to sleep -
        # chirping every time you walk past would be maddening.
        err = stt.last_error()
        if err and "timed out" not in err:
            print(f"[voice] speech recognition unavailable: {err}")
        _set_state(SLEEPING)
        return

    _last_heard = text

    # --- PROCESSING ----------------------------------------------------
    _set_state(PROCESSING)
    command = commands.match_command(text)

    if command is not None and _on_command:
        _on_command(command, text)

    if command is not None:
        reply = commands.reply_for(command, text)
    else:
        reply = (_NOT_UNDERSTOOD_HI
                 if commands.reply_language(text) == "hi-IN"
                 else _NOT_UNDERSTOOD_EN)

    # --- SPEAKING ------------------------------------------------------
    _set_state(SPEAKING)
    tts.say(reply, commands.reply_language(text))

    # Wait for the reply to finish, but let a new wake word cut it short.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if _stop_flag.is_set() or _wake_event.is_set():
            break
        if tts.is_speaking():
            time.sleep(0.05)
            continue
        # Give the queue a beat to pick the sentence up before deciding
        # it has finished.
        time.sleep(0.15)
        if not tts.is_speaking():
            break

    _set_state(SLEEPING)


def _remember_partial(text):
    global _last_partial
    _last_partial = text


def _run():
    while not _stop_flag.is_set():
        _set_state(SLEEPING)
        if not _wake_event.wait(timeout=0.2):
            continue
        _wake_event.clear()
        try:
            _handle_one_turn()
        except Exception as exc:
            # A failure here must never take the robot down.
            print(f"[voice] turn failed: {type(exc).__name__}: {exc}")
            _set_state(SLEEPING)


def start(on_command):
    """
    Start the voice loop. Returns True if it is actually usable.

    Degrades in stages rather than failing outright:
      no microphone  -> voice off entirely, keyboard still works
      no wake word   -> voice off (we refuse to run STT continuously)
      no Sarvam key  -> wake word still works, but nothing can be understood
    """
    global _thread, _on_command, _enabled

    _on_command = on_command

    if not audio_in.is_available():
        print("[voice] No microphone, so voice control is off.")
        return False

    if not wakeword.is_ready():
        print("[voice] No wake word, so voice control is off.")
        print("        (We deliberately do not run speech recognition"
              " continuously.)")
        return False

    if not stt.is_configured():
        print("[voice] SARVAM_API_KEY is not set: the robot will wake up but")
        print("        will not be able to understand anything you say.")

    wakeword.listen(_on_wake)
    _stop_flag.clear()
    _thread = threading.Thread(target=_run, name="voice", daemon=True)
    _thread.start()
    _enabled = True
    return True


def is_enabled():
    return _enabled


def stop():
    global _thread
    if _thread is None:
        return
    _stop_flag.set()
    _wake_event.set()
    _thread.join(timeout=3.0)
    _thread = None
