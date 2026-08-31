"""
speech.py
---------
LEGACY - no longer used by main.py.

This was the original voice input: a hand-tuned loudness threshold feeding
clips to a local Whisper model. It has been replaced by the wake-word +
Sarvam pipeline (wakeword.py -> stt.py, orchestrated by voice.py), because
the threshold approach was fragile in a noisy room and could not handle
Hindi or Hinglish.

It is kept because it is the only fully offline speech recogniser here, and
it still runs standalone (`python speech.py`) if you want to compare. Nothing
imports it. Delete it if you would rather not carry the faster-whisper
dependency.

Original description follows.

Microphone -> local Whisper -> "follow me" / "stop"

This runs on its own thread so the webcam loop never waits for it:

    mic  ->  0.1s audio blocks  ->  is anyone talking? (loudness)
         ->  collect one utterance  ->  Whisper  ->  keyword match  ->  [queue]

main.py just calls get_command() once per frame and gets either None or a
command string. It never blocks.

Two practical details worth knowing:

* We do our own "is someone talking" test using loudness (RMS) rather than
  transcribing continuously. Whisper on a laptop CPU is far too slow to run
  every 0.1s, and it invents words when fed silence.
* While the robot is speaking we throw the audio away, otherwise the mic
  picks up "Okay, following you." and Whisper hears the word "following".
"""

import os
import queue
import re
import threading
import time
from collections import deque

# Windows without Developer Mode can't make symlinks, and the model cache
# prints a long warning about it every run. It works fine, so silence it.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import sounddevice as sd

import behavior

SAMPLE_RATE = 16000          # Whisper always works at 16 kHz
BLOCK_SECONDS = 0.1
BLOCK_FRAMES = int(SAMPLE_RATE * BLOCK_SECONDS)

SILENCE_TO_END = 0.6         # seconds of quiet that mean "they stopped talking"
MIN_UTTERANCE = 0.25         # ignore clicks and door bumps shorter than this
MAX_UTTERANCE = 4.0          # never collect more than this before transcribing
PREROLL_BLOCKS = 3           # keep 0.3s from *before* the trigger, so the
                             # first syllable isn't clipped off
COMMAND_COOLDOWN = 1.5       # ignore a repeat of the same command within this
MIN_THRESHOLD = 0.0035       # loudness floor for "someone is talking";
                             # lower it if the robot never hears you
MAX_THRESHOLD = 0.05         # and never go above this, or normal speech
                             # (~0.02-0.20) would be ignored completely
RECALIBRATE_EVERY = 10.0     # seconds between re-estimates of the noise floor

_command_queue = queue.Queue()
_worker = None
_stop_flag = threading.Event()
_ready = threading.Event()
_failed = threading.Event()


def match_command(text):
    """
    Turn a raw Whisper transcript into one of our two commands, or None.

    Deliberately lenient: Whisper writes "Follow me." or "follow me, robot"
    or "Stop!" depending on how you say it, so we look for keywords rather
    than demanding an exact string.

    "stop" is checked first on purpose, so "stop following me" means STOP.
    """
    clean = re.sub(r"[^a-z ]", " ", text.lower())
    words = clean.split()

    if "stop" in words:
        return behavior.CMD_STOP
    if "follow" in words or "following" in words:
        return behavior.CMD_FOLLOW_ME
    return None


def _measure_noise_floor(stream, seconds=1.0):
    """
    Listen to the empty room for a moment and learn how loud "quiet" is.

    Without this, a fixed threshold is either deaf in a noisy office or
    permanently triggered by a laptop fan.

    Measured on this laptop: the Intel Smart Sound mic array applies its own
    noise suppression, so an idle room reads about 0.00004 - far quieter than
    a typical mic. Normal speech is around 0.02-0.20. MIN_THRESHOLD sits well
    between the two: high enough to ignore the room, low enough to hear you
    without shouting.
    """
    levels = []
    for _ in range(int(seconds / BLOCK_SECONDS)):
        block, _ = stream.read(BLOCK_FRAMES)
        levels.append(float(np.sqrt(np.mean(block ** 2))))
    return threshold_from(levels)


def threshold_from(levels):
    """
    Turn a list of block loudnesses into a speech threshold.

    Two guards, both learned the hard way:

    * The 20th percentile, not the median. If someone is talking (or a video
      is playing) while we calibrate, the median is a *speech* level and the
      threshold ends up above your voice - the mic then appears completely
      deaf. The 20th percentile looks at the quieter moments instead.
    * A hard ceiling. Even the percentile can be fooled by continuous noise,
      so we never set the bar higher than MAX_THRESHOLD, because normal speech
      lives around 0.02-0.20 and anything above 0.05 would ignore all of it.
    """
    quiet = float(np.percentile(levels, 20))
    return min(max(quiet * 6.0, MIN_THRESHOLD), MAX_THRESHOLD)


def _run(model_size, device_index, is_muted, debug, threshold_override):
    """The listener thread. Loads Whisper, then loops until told to stop."""
    from faster_whisper import WhisperModel

    # int8 on CPU is the fast, low-memory setting. On this laptop there is no
    # usable GPU, so CPU is the only sensible choice anyway.
    try:
        print(f"[speech] Loading Whisper model '{model_size}' (first run downloads it)...")
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4)
    except Exception as exc:
        print(f"[speech] Could not load the Whisper model: {exc}")
        _failed.set()
        _ready.set()
        return

    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="float32", blocksize=BLOCK_FRAMES,
                                device=device_index)
        stream.start()
    except Exception as exc:
        print(f"[speech] Could not open the microphone: {exc}")
        print("[speech] Check Settings > Privacy & security > Microphone.")
        _failed.set()
        _ready.set()
        return

    try:
        measured = _measure_noise_floor(stream)
        threshold = threshold_override if threshold_override else measured
        print(f"[speech] Listening. (loudness threshold {threshold:.5f})")
        _ready.set()

        last_meter = 0.0  # for the debug level meter

        # Keep the recent history of how loud the room is, so we can re-derive
        # the threshold as conditions change - someone starts a video call,
        # the office empties out, a fan switches on. Without this, one bad
        # second at startup would deafen us for the whole session.
        recent_levels = deque(maxlen=int(30 / BLOCK_SECONDS))  # last 30s
        last_recalibrate = time.time()

        preroll = []          # rolling buffer of the last few quiet blocks
        collected = []        # blocks belonging to the current utterance
        in_speech = False
        silent_blocks = 0
        last_command = None
        last_command_time = 0.0

        while not _stop_flag.is_set():
            block, _ = stream.read(BLOCK_FRAMES)
            samples = block[:, 0]

            # --- mute ourselves while the robot is talking ---
            if is_muted():
                in_speech = False
                collected = []
                preroll = []
                continue

            level = float(np.sqrt(np.mean(samples ** 2)))
            loud = level > threshold

            # A once-per-second level meter, so you can see whether the mic
            # hears you at all and whether the threshold is set sensibly.
            if debug and time.time() - last_meter > 1.0:
                last_meter = time.time()
                bars = min(int(level / max(threshold, 1e-9) * 10), 40)
                print(f"[speech] level {level:.5f} {'#' * bars}"
                      f"{' (LOUD)' if loud else ''}")

            # Re-derive the threshold every few seconds from what the room has
            # actually sounded like. Skipped if the user pinned a value.
            recent_levels.append(level)
            if (threshold_override is None
                    and time.time() - last_recalibrate > RECALIBRATE_EVERY
                    and len(recent_levels) > 50 and not in_speech):
                last_recalibrate = time.time()
                new_threshold = threshold_from(list(recent_levels))
                if abs(new_threshold - threshold) > 1e-6:
                    if debug:
                        print(f"[speech] threshold {threshold:.5f} -> {new_threshold:.5f}")
                    threshold = new_threshold

            if not in_speech:
                # Keep a short history so we don't clip the start of the word.
                preroll.append(samples)
                if len(preroll) > PREROLL_BLOCKS:
                    preroll.pop(0)
                if loud:
                    in_speech = True
                    collected = list(preroll) + [samples]
                    preroll = []
                    silent_blocks = 0
                continue

            # --- we are inside an utterance ---
            collected.append(samples)
            silent_blocks = 0 if loud else silent_blocks + 1

            long_enough_pause = silent_blocks * BLOCK_SECONDS >= SILENCE_TO_END
            too_long = len(collected) * BLOCK_SECONDS >= MAX_UTTERANCE
            if not (long_enough_pause or too_long):
                continue

            # --- utterance finished: transcribe it ---
            in_speech = False
            audio = np.concatenate(collected)
            collected = []

            if len(audio) / SAMPLE_RATE < MIN_UTTERANCE:
                continue

            # A single keyboard click or door bump can nudge past the
            # threshold for one block. Whisper invents words when you feed it
            # noise, and an invented "stop" would halt the robot for no reason.
            # Real speech is far louder than the trigger level, so insist the
            # utterance actually got loud before we bother transcribing it.
            peak = float(np.abs(audio).max())
            if peak < threshold * 3.0:
                if debug:
                    print(f"[speech] ignored a quiet blip (peak {peak:.5f})")
                continue

            try:
                segments, _info = model.transcribe(
                    audio, language="en", beam_size=1, temperature=0.0,
                    condition_on_previous_text=False,
                )
                text = " ".join(s.text for s in segments).strip()
            except Exception as exc:
                print(f"[speech] Transcription failed: {exc}")
                text = ""

            if debug and text:
                print(f"[speech] heard: {text!r}")

            command = match_command(text)
            now = time.time()
            if command is not None:
                # One utterance produces one command. The cooldown is a second
                # safety net so a long "stoooop" split into two chunks doesn't
                # fire twice.
                if command == last_command and now - last_command_time < COMMAND_COOLDOWN:
                    pass
                else:
                    _command_queue.put((command, text))
                    last_command = command
                    last_command_time = now

            # Transcribing took a moment and the mic kept recording. Throw that
            # backlog away so we stay in the present instead of falling behind.
            if stream.read_available:
                stream.read(stream.read_available)

    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


def start(model_size="tiny.en", device_index=None, is_muted=lambda: False,
          debug=False, threshold=None):
    """
    Start listening in the background.

    Returns immediately. Whisper loads on the worker thread, so call
    wait_until_ready() if you want to know when the mic is actually live.
    """
    global _worker
    if _worker is not None:
        return
    _stop_flag.clear()
    _worker = threading.Thread(
        target=_run,
        args=(model_size, device_index, is_muted, debug, threshold),
        name="speech", daemon=True)
    _worker.start()


def wait_until_ready(timeout=180):
    """Block until the model is loaded and the mic is open. True if it worked."""
    _ready.wait(timeout=timeout)
    return not _failed.is_set()


def is_available():
    """False if the microphone or the model could not be started."""
    return not _failed.is_set()


def get_command():
    """
    Return (command, raw_text) if something was recognised since the last call,
    otherwise (None, None). Never blocks.
    """
    try:
        return _command_queue.get_nowait()
    except queue.Empty:
        return None, None


def stop():
    """Stop the listener thread."""
    global _worker
    if _worker is None:
        return
    _stop_flag.set()
    _worker.join(timeout=3.0)
    _worker = None


# Microphone-only test:  python speech.py
# Say "follow me" and "stop" and watch what Whisper hears.
# A level meter prints once a second so you can confirm the mic hears you.
if __name__ == "__main__":
    import sys
    thr = float(sys.argv[1]) if len(sys.argv) > 1 else None
    start(debug=True, threshold=thr)
    if not wait_until_ready():
        raise SystemExit("microphone or model unavailable")
    print("Say 'follow me' or 'stop'. Ctrl+C to quit.")
    try:
        while True:
            cmd, text = get_command()
            if cmd:
                print(f"  --> COMMAND: {cmd}   (from {text!r})")
            time.sleep(0.05)
    except KeyboardInterrupt:
        stop()
