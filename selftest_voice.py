"""
selftest_voice.py
-----------------
Test the voice stack one stage at a time, in the order things can break.

Run this BEFORE main.py whenever voice misbehaves. It tells you which single
stage is at fault instead of leaving you guessing at the whole chain.

    python selftest_voice.py            # all stages
    python selftest_voice.py 3          # just stage 3

Stages
    1  configuration and keys
    2  microphone actually captures audio
    3  Porcupine hears the wake word
    4  Sarvam speech-to-text, English
    5  Sarvam speech-to-text, Hindi
    6  Sarvam text-to-speech, English
    7  Sarvam text-to-speech, Hindi
    8  command matching (offline, no hardware)
"""

import sys
import time

import numpy as np

import audio_in
import commands
import config
import stt
import tts
import wakeword

results = {}


def record(stage, ok, note=""):
    results[stage] = (ok, note)
    print(f"\n  ==> stage {stage}: {'PASS' if ok else 'FAIL'} {note}\n")


def banner(n, title):
    print("=" * 64)
    print(f"  STAGE {n}: {title}")
    print("=" * 64)


# ---------------------------------------------------------------- stage 1
def stage1():
    banner(1, "configuration")
    for line in config.describe():
        print("  " + line)
    ok = bool(config.PICOVOICE_ACCESS_KEY) and bool(config.SARVAM_API_KEY)
    missing = []
    if not config.PICOVOICE_ACCESS_KEY:
        missing.append("PICOVOICE_ACCESS_KEY")
    if not config.SARVAM_API_KEY:
        missing.append("SARVAM_API_KEY")
    record(1, ok, f"missing: {', '.join(missing)}" if missing else "")
    return ok


# ---------------------------------------------------------------- stage 2
def stage2():
    banner(2, "microphone captures audio")
    if not audio_in.is_available() and not audio_in.start(blocksize=512):
        record(2, False, f"mic would not open: {audio_in.last_error()}")
        return False

    peaks = []
    audio_in.add_frame_listener(
        lambda s: peaks.append(float(np.sqrt(np.mean((s / 32768.0) ** 2)))))
    print("  Make some noise for 6 seconds...")
    end = time.time() + 6
    while time.time() < end:
        time.sleep(0.5)
        recent = peaks[-40:] or [0.0]
        print(f"    level {max(recent):.5f} {'#' * min(int(max(recent) * 200), 45)}")

    loudest = max(peaks) if peaks else 0.0
    ok = loudest > 0.0008
    record(2, ok, f"loudest {loudest:.5f}"
           + ("" if ok else " - mic is muted or blocked in Windows"))
    return ok


# ---------------------------------------------------------------- stage 3
def stage3():
    banner(3, "wake word")
    if not wakeword.is_ready() and not wakeword.init():
        record(3, False, "Porcupine unavailable, see message above")
        return False
    if not audio_in.is_available():
        audio_in.start(blocksize=wakeword.frame_length())

    hits = []
    wakeword.listen(lambda: hits.append(time.time()))
    name = wakeword.active_name()
    print(f"  Say '{name}' three times. 30 seconds.")
    if name != config.WAKE_WORD:
        print(f"  NOTE: '{config.WAKE_WORD}' needs a custom .ppn; using "
              f"'{name}' for now.")
    end = time.time() + 30
    seen = 0
    while time.time() < end:
        time.sleep(0.2)
        if len(hits) > seen:
            seen = len(hits)
            print(f"    >>> heard it ({seen})")

    ok = len(hits) >= 1
    record(3, ok, f"{len(hits)} detection(s)")
    return ok


# ------------------------------------------------------------- stage 4 / 5
def _stt_stage(n, language_label, prompt_text):
    banner(n, f"speech to text, {language_label}")
    if not stt.is_configured():
        record(n, False, "SARVAM_API_KEY not set")
        return False
    if not audio_in.is_available():
        audio_in.start(blocksize=512)

    print(f"  Say: {prompt_text}")
    for count in (3, 2, 1):
        print(f"    {count}...")
        time.sleep(1)
    print("  SPEAK NOW")

    audio_in.arm_capture()
    text = stt.listen_once(on_partial=lambda t: print(f"    ...{t}"))
    audio_in.disarm_capture()

    print(f"  transcript : {text!r}")
    print(f"  command    : {commands.match_command(text or '')}")
    ok = bool(text)
    record(n, ok, "" if ok else f"error: {stt.last_error()}")
    return ok


# ------------------------------------------------------------- stage 6 / 7
def _tts_stage(n, language_label, text, language_code):
    banner(n, f"text to speech, {language_label}")
    if not config.SARVAM_API_KEY:
        record(n, False, "SARVAM_API_KEY not set (offline voice would be used)")
        return False

    tts.start()
    print(f"  Speaking: {text}")
    tts.say(text, language_code)
    time.sleep(0.5)
    waited = 0.0
    while tts.is_speaking() and waited < 25:
        time.sleep(0.2)
        waited += 0.2

    err = tts.last_error()
    ok = err is None
    record(n, ok, "" if ok else f"error: {err}")
    return ok


# ---------------------------------------------------------------- stage 8
def stage8():
    banner(8, "command matching (offline)")
    import test_commands
    failed = test_commands.main()
    record(8, failed == 0, "")
    return failed == 0


STAGES = {
    1: stage1,
    2: stage2,
    3: stage3,
    4: lambda: _stt_stage(4, "English", '"follow me"'),
    5: lambda: _stt_stage(5, "Hindi", '"मेरे साथ चलो"  (mere saath chalo)'),
    6: lambda: _tts_stage(6, "English", "Okay, following you.", "en-IN"),
    7: lambda: _tts_stage(7, "Hindi", "ठीक है, मैं आपके साथ चल रहा हूँ।", "hi-IN"),
    8: stage8,
}


def main():
    wanted = [int(a) for a in sys.argv[1:] if a.isdigit()] or sorted(STAGES)
    for n in wanted:
        try:
            STAGES[n]()
        except KeyboardInterrupt:
            print("\ninterrupted")
            break
        except Exception as exc:
            record(n, False, f"{type(exc).__name__}: {exc}")

    print("=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    for n in sorted(results):
        ok, note = results[n]
        print(f"  stage {n}: {'PASS' if ok else 'FAIL'}  {note}")

    audio_in.stop()
    wakeword.shutdown()
    tts.shutdown()
    return 0 if all(ok for ok, _ in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
