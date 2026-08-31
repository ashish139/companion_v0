"""
Offline test of the whole local voice chain, without needing anyone to speak.

We render phrases with the Windows voice, then push that audio through the
exact same path a microphone recording would take:

    audio -> Silero VAD -> Whisper -> wake word split -> command matching

The only thing this does NOT cover is the microphone itself, which
`audio_in.py` tests on its own.

    python test_local_voice.py
"""

import os
import tempfile
import wave

import numpy as np

import behavior
import commands
import local_stt

FOLLOW = behavior.CMD_FOLLOW_ME
STOP = behavior.CMD_STOP

# (spoken phrase, expect wake word?, expected command)
CASES = [
    ("Golu follow me",              True,  FOLLOW),
    ("Golu stop",                   True,  STOP),
    ("follow me",                   False, FOLLOW),
    ("stop",                        False, STOP),
    # things the robot must ignore
    ("I follow football",           False, None),
    ("we should stop for lunch",    False, None),
    ("what time is the meeting",    False, None),
]


def render(phrase, path):
    """Speak a phrase to a wav file using the offline Windows voice."""
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", 165)
    engine.save_to_file(phrase, path)
    engine.runAndWait()
    engine.stop()


def load_16k(path):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
    audio = data.astype(np.float32) / 32768.0
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio


def main():
    if not local_stt.init():
        raise SystemExit("could not load the offline recogniser")

    tmp = tempfile.mkdtemp(prefix="voicetest_")
    failures = 0

    print(f"{'spoken':32} {'transcript':34} wake  command")
    print("-" * 92)

    for phrase, want_wake, want_cmd in CASES:
        path = os.path.join(tmp, phrase.replace(" ", "_") + ".wav")
        render(phrase, path)
        audio = load_16k(path)

        # Exactly the path the live listener takes: same VAD, same Whisper
        # call, same initial_prompt.
        speech = local_stt._contains_speech(audio)
        transcript = "" if speech is None else local_stt.transcribe(speech)

        heard_wake, remainder = commands.split_wake_word(transcript)
        spoken = remainder if heard_wake else transcript
        got_cmd = commands.match_command(spoken)

        ok = (heard_wake == want_wake) and (got_cmd == want_cmd)
        if not ok:
            failures += 1
        print(f"[{'ok  ' if ok else 'FAIL'}] {phrase:26} {transcript!r:34} "
              f"{str(heard_wake):5} {str(got_cmd):10} "
              f"(want {want_wake}/{want_cmd})")

    # Whisper repetition loops must be caught. This exact transcript came out
    # of a real microphone capture.
    print("\n--- repetition-loop rejection ---")
    loop_cases = [
        ("Good luck. " * 40, True),
        ("Golu, follow me.", False),
        ("stop", False),
        ("we should stop for lunch later today", False),
        ("yeah yeah yeah yeah yeah yeah yeah yeah yeah yeah", True),
    ]
    for text, want in loop_cases:
        got = local_stt.looks_degenerate(text)
        ok = got == want
        if not ok:
            failures += 1
        print(f"[{'ok  ' if ok else 'FAIL'}] {text[:44]!r:48} degenerate={got}")

    # Silero must reject pure noise rather than letting Whisper invent words.
    print("\n--- non-speech rejection ---")
    rng = np.random.default_rng(0)
    for label, audio in (
        ("silence", np.zeros(16000 * 2, dtype=np.float32)),
        ("white noise", (rng.standard_normal(16000 * 2) * 0.05).astype(np.float32)),
    ):
        speech = local_stt._contains_speech(audio)
        ok = speech is None
        if not ok:
            failures += 1
        print(f"[{'ok  ' if ok else 'FAIL'}] {label:14} -> "
              f"{'rejected' if ok else 'PASSED THROUGH (would hallucinate)'}")

    total = len(CASES) + len(loop_cases) + 2
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
