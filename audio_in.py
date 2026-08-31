"""
audio_in.py
-----------
One microphone, two consumers.

The wake-word detector needs *every* audio frame, all the time. The speech
recogniser only needs audio after you've said the wake word. You cannot open
the same microphone twice on Windows, so this module owns the single input
stream and hands the audio to whoever wants it:

    mic ──► one reader thread ──┬──► frame listeners  (wake word, always on)
                                └──► capture queue    (STT, only when armed)

Audio is 16 kHz, mono, int16 - the format both Porcupine and Sarvam expect.
"""

import queue
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000

_stream = None
_thread = None
_stop_flag = threading.Event()
_failed = threading.Event()

# Called with every block of audio. Used by the wake-word detector.
_listeners = []
_listeners_lock = threading.Lock()

# Audio destined for the speech recogniser. Only filled while armed.
_capture_queue = queue.Queue()
_capturing = threading.Event()

_last_error = None


def add_frame_listener(fn):
    """
    Register a function called with every block of audio (int16 numpy array).

    Keep the function fast - it runs on the audio thread. Anything slow will
    make the microphone drop blocks.
    """
    with _listeners_lock:
        _listeners.append(fn)


def _run(device, blocksize):
    """Reader thread: pull blocks off the mic and pass them on."""
    global _stream, _last_error
    try:
        _stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                 dtype="int16", blocksize=blocksize,
                                 device=device)
        _stream.start()
    except Exception as exc:
        _last_error = str(exc)
        print(f"[audio] Could not open the microphone: {exc}")
        print("[audio] Check the mic mute key and Windows privacy settings.")
        _failed.set()
        return

    while not _stop_flag.is_set():
        try:
            block, overflowed = _stream.read(blocksize)
        except Exception as exc:
            _last_error = str(exc)
            print(f"[audio] Microphone read failed: {exc}")
            _failed.set()
            break

        samples = block[:, 0]  # mono

        # Wake word and anything else that watches continuously.
        with _listeners_lock:
            current = list(_listeners)
        for fn in current:
            try:
                fn(samples)
            except Exception as exc:
                # A broken listener must not kill the microphone.
                print(f"[audio] frame listener error: {exc}")

        # Speech recogniser, only while we're actually listening.
        if _capturing.is_set():
            _capture_queue.put(samples.copy())

    try:
        _stream.stop()
        _stream.close()
    except Exception:
        pass
    _stream = None


def start(device=None, blocksize=512):
    """
    Open the microphone and start reading.

    blocksize should match Porcupine's frame_length (512) so the wake-word
    detector gets exactly the frame size it expects.

    Returns True if the mic opened.
    """
    global _thread
    if _thread is not None:
        return not _failed.is_set()

    _stop_flag.clear()
    _failed.clear()
    _thread = threading.Thread(target=_run, args=(device, blocksize),
                               name="audio_in", daemon=True)
    _thread.start()

    # Give the stream a moment to fail loudly rather than silently.
    _thread.join(timeout=2.0)
    return not _failed.is_set()


def is_available():
    return _thread is not None and not _failed.is_set()


def last_error():
    return _last_error


def arm_capture():
    """Start collecting audio for the speech recogniser."""
    drain_capture()
    _capturing.set()


def disarm_capture():
    """Stop collecting. Anything already queued stays until drained."""
    _capturing.clear()


def drain_capture():
    """Throw away buffered audio, so a new utterance starts clean."""
    while True:
        try:
            _capture_queue.get_nowait()
        except queue.Empty:
            return


def read_captured(timeout=0.1):
    """
    Return the next captured block as int16 numpy, or None if nothing came
    within `timeout` seconds. Never blocks for long.
    """
    try:
        return _capture_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def stop():
    """Close the microphone."""
    global _thread
    if _thread is None:
        return
    _stop_flag.set()
    _thread.join(timeout=3.0)
    _thread = None


# Quick check:  python audio_in.py
if __name__ == "__main__":
    import time

    print("Opening microphone...")
    if not start():
        raise SystemExit(f"FAILED: {last_error()}")

    levels = []
    add_frame_listener(lambda s: levels.append(float(np.sqrt(np.mean((s / 32768.0) ** 2)))))

    print("Listening for 6 seconds - make some noise.")
    end = time.time() + 6
    while time.time() < end:
        time.sleep(0.5)
        recent = levels[-30:] or [0.0]
        peak = max(recent)
        print(f"  level {peak:.5f} {'#' * min(int(peak * 200), 50)}")

    stop()
    overall = max(levels) if levels else 0.0
    print(f"\nloudest block: {overall:.5f}")
    print("MIC OK" if overall > 0.0008 else "MIC SILENT - check the mute key")
