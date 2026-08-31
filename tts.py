"""
tts.py
------
Speaking out loud, without freezing the video loop.

pyttsx3's engine.runAndWait() blocks until the sentence has finished playing.
If we called that from the webcam loop the picture would visibly freeze for a
second every time the robot talks. So instead:

    main loop  ->  say("Stopping.")  ->  [queue]  ->  worker thread  ->  speaker

The worker thread also owns the engine object. That matters on Windows,
because the SAPI5 voice is a COM object and COM objects must be created and
used on the same thread.
"""

import queue
import threading
import time

_say_queue = queue.Queue()
_worker = None
_stop_flag = threading.Event()

# True while a sentence is actually playing. main.py uses this to mute the
# microphone so the robot doesn't hear itself say "Stopping." and react to it.
_speaking = threading.Event()

# Small grace period after speaking, to let the room echo die down.
_quiet_until = 0.0

_TAIL_SECONDS = 0.4


def _run(rate, voice_hint):
    """The worker thread: create the engine once, then drain the queue forever."""
    global _quiet_until

    # comtypes/SAPI5 wants COM initialised on whichever thread uses it.
    # On some machines pyttsx3 does this itself, so failure here is harmless.
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    try:
        import pyttsx3
        engine = pyttsx3.init()
    except Exception as exc:
        print(f"[tts] Could not start the speech engine ({exc}).")
        print("[tts] Falling back to printing what the robot would say.")
        engine = None

    if engine is not None:
        if rate is not None:
            engine.setProperty("rate", rate)
        if voice_hint:
            # Pick the first installed voice whose name contains the hint,
            # e.g. --voice zira. If nothing matches we keep the default voice.
            for v in engine.getProperty("voices"):
                if voice_hint.lower() in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break

    while not _stop_flag.is_set():
        try:
            text = _say_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        if text is None:  # shutdown sentinel
            break

        _speaking.set()
        try:
            if engine is None:
                print(f"[robot says] {text}")
                time.sleep(0.6)  # pretend it took a moment, so muting still works
            else:
                engine.say(text)
                engine.runAndWait()
        except Exception as exc:
            print(f"[tts] Failed to speak {text!r}: {exc}")
        finally:
            _quiet_until = time.time() + _TAIL_SECONDS
            _speaking.clear()

    if engine is not None:
        try:
            engine.stop()
        except Exception:
            pass


def start(rate=175, voice_hint=None):
    """Start the speaking thread. Safe to call once at startup."""
    global _worker
    if _worker is not None:
        return
    _stop_flag.clear()
    _worker = threading.Thread(target=_run, args=(rate, voice_hint),
                               name="tts", daemon=True)
    _worker.start()


def say(text):
    """Queue a sentence. Returns immediately - it does not wait for the audio."""
    if text:
        _say_queue.put(text)


def is_speaking():
    """True while audio is playing (plus a short tail). Used to mute the mic."""
    return _speaking.is_set() or time.time() < _quiet_until


def shutdown():
    """Ask the worker thread to finish and wait briefly for it."""
    global _worker
    if _worker is None:
        return
    _stop_flag.set()
    _say_queue.put(None)
    _worker.join(timeout=3.0)
    _worker = None


# Quick check that your speakers work:  python tts.py
if __name__ == "__main__":
    start()
    say("Okay, following you.")
    say("Stopping.")
    while is_speaking() or not _say_queue.empty():
        time.sleep(0.1)
    time.sleep(0.3)
    shutdown()
    print("done")
