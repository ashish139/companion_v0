"""
wakeword.py
-----------
Always-on, local, lightweight detection of the robot's name.

Porcupine runs on the CPU using almost nothing (it is a tiny purpose-built
model, not a general speech recogniser). That is the whole point: we can leave
it running forever, and only wake the expensive Sarvam speech recogniser once
the robot has actually been called.

Nothing here touches the network.

About the name "Milo"
---------------------
Porcupine ships a fixed list of built-in keywords and "Milo" is not one of
them. To really wake on "Milo" you train a custom keyword (free) at
https://console.picovoice.ai, download the Windows .ppn, and set
WAKE_WORD_PPN in your .env. Until then we fall back to a built-in word so the
rest of the system is testable.
"""

import os

import numpy as np

import audio_in
import config

_porcupine = None
_on_wake = None
_active_name = None
_enabled = False


def init():
    """
    Create the detector. Returns True on success.

    Never raises - if the key is missing or the model won't load we print a
    clear message and the app carries on without a wake word.
    """
    global _porcupine, _active_name

    if not config.PICOVOICE_ACCESS_KEY:
        print("[wake] PICOVOICE_ACCESS_KEY is not set - wake word disabled.")
        print("       Get a free key at https://console.picovoice.ai and put")
        print("       it in your .env file. See .env.example.")
        return False

    try:
        import pvporcupine
    except ImportError:
        print("[wake] pvporcupine is not installed - wake word disabled.")
        return False

    try:
        if config.WAKE_WORD_PPN:
            if not os.path.exists(config.WAKE_WORD_PPN):
                print(f"[wake] WAKE_WORD_PPN points at a missing file: "
                      f"{config.WAKE_WORD_PPN}")
                return False
            _porcupine = pvporcupine.create(
                access_key=config.PICOVOICE_ACCESS_KEY,
                keyword_paths=[config.WAKE_WORD_PPN],
                sensitivities=[config.WAKE_SENSITIVITY],
            )
            _active_name = config.WAKE_WORD
        else:
            # No custom model yet, so use a built-in word.
            _porcupine = pvporcupine.create(
                access_key=config.PICOVOICE_ACCESS_KEY,
                keywords=[config.WAKE_WORD_FALLBACK],
                sensitivities=[config.WAKE_SENSITIVITY],
            )
            _active_name = config.WAKE_WORD_FALLBACK
            print(f"[wake] No custom .ppn supplied, so the wake word is "
                  f"'{_active_name}', not '{config.WAKE_WORD}'.")
            print( "       Train 'Milo' at https://console.picovoice.ai and set "
                   "WAKE_WORD_PPN to use the real name.")
    except Exception as exc:
        print(f"[wake] Could not start Porcupine: {exc}")
        print("       A bad or expired AccessKey is the usual cause.")
        return False

    print(f"[wake] Ready. Say '{_active_name}' to wake the robot.")
    return True


def frame_length():
    """How many samples Porcupine wants per call. Usually 512."""
    return _porcupine.frame_length if _porcupine else 512


def _on_audio(samples):
    """Audio-thread callback. Must stay fast."""
    if not _enabled or _porcupine is None:
        return
    # Porcupine wants exactly frame_length int16 samples.
    if len(samples) != _porcupine.frame_length:
        return
    try:
        if _porcupine.process(samples) >= 0 and _on_wake:
            _on_wake()
    except Exception as exc:
        print(f"[wake] detection error: {exc}")


def listen(on_wake):
    """Start calling on_wake() whenever the name is heard."""
    global _on_wake, _enabled
    _on_wake = on_wake
    _enabled = True
    audio_in.add_frame_listener(_on_audio)


def set_enabled(enabled):
    """
    Turn detection on or off without tearing anything down.

    We deliberately leave it ON while the robot is speaking - that is what
    makes barge-in work. The robot never says its own name, so it cannot
    trigger itself.
    """
    global _enabled
    _enabled = enabled


def active_name():
    """The word that actually wakes it, which may differ from WAKE_WORD."""
    return _active_name


def is_ready():
    return _porcupine is not None


def shutdown():
    global _porcupine
    if _porcupine is not None:
        try:
            _porcupine.delete()
        except Exception:
            pass
        _porcupine = None


# Standalone test:  python wakeword.py
if __name__ == "__main__":
    import time

    if not init():
        raise SystemExit("wake word unavailable - see the message above")

    if not audio_in.start(blocksize=frame_length()):
        raise SystemExit(f"microphone unavailable: {audio_in.last_error()}")

    hits = []
    listen(lambda: (hits.append(time.time()),
                    print(f"  >>> WAKE #{len(hits)} heard at {time.strftime('%H:%M:%S')}")))

    print(f"\nSay '{active_name()}' a few times. 45 seconds. Ctrl+C to stop early.\n")
    try:
        end = time.time() + 45
        while time.time() < end:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    audio_in.stop()
    shutdown()
    print(f"\nheard the wake word {len(hits)} time(s)")
