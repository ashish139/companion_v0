"""
What does Whisper actually write when it hears the robot's name?

Whisper has never heard of "Golu", so it guesses at the spelling, and the
guess depends on how the name is said. This renders the name several ways,
runs each through the real recogniser, and reports whether the transcript
would be accepted as the wake word.

Use it whenever you rename the robot, or if it stops answering to its name.
Anything reported as NOT MATCHED should be added to WAKE_VARIANTS in your
.env (comma-separated) or to _KNOWN_VARIANTS in commands.py.

    python test_wake_variants.py
"""

import os
import tempfile
import wave

import numpy as np

import commands
import config
import local_stt

# Spellings fed to the Windows voice, to get a few different pronunciations
# of the same name out of it.
PRONUNCIATIONS = [
    "Golu",
    "Gollu",
    "Goloo",
    "Go Lu",
    "Golu, follow me",
    "Golu, stop",
    "Hey Golu",
]


def render(phrase, path):
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


def transcribe(audio):
    """Same VAD + Whisper path the live listener uses, prompt included."""
    speech = local_stt._contains_speech(audio)
    return "" if speech is None else local_stt.transcribe(speech)


def main():
    import sys
    model_size = sys.argv[1] if len(sys.argv) > 1 else "tiny.en"
    if not local_stt.init(model_size=model_size):
        raise SystemExit("could not load the offline recogniser")
    print(f"model            : {model_size}")

    print(f"wake word        : {config.WAKE_WORD}")
    print(f"accepted variants: {', '.join(commands.WAKE_VARIANTS)}")
    print()

    tmp = tempfile.mkdtemp(prefix="wakevariants_")
    missed = []

    print(f"{'said as':22} {'Whisper wrote':38} matched?")
    print("-" * 78)
    for phrase in PRONUNCIATIONS:
        path = os.path.join(tmp, phrase.replace(" ", "_").replace(",", "") + ".wav")
        render(phrase, path)
        text = transcribe(load_16k(path))
        heard, remainder = commands.split_wake_word(text)
        flag = "yes" if heard else "NOT MATCHED"
        if not heard:
            missed.append(text)
        extra = f"  -> command={commands.match_command(remainder)}" if heard and remainder else ""
        print(f"  {phrase:20} {text!r:38} {flag}{extra}")

    print()
    if missed:
        print("These were not recognised as the name. If you say it that way,")
        print("add the spelling to WAKE_VARIANTS in .env:")
        for text in missed:
            print(f"    {text!r}")
    else:
        print("Every pronunciation was recognised.")

    print()
    print("NOTE: this uses the synthetic Windows voice, which does not sound")
    print("like you. Confirm with a real voice using:")
    print("    python main.py --debug-audio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
