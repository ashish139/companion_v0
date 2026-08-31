# companion_v0 — companion robot brain (laptop prototype)

A laptop-only demo of the "brain" a following robot would need. There is no
robot: the movement commands are printed instead of driven.

```
webcam ──► YOLO person detector ──► LEFT / CENTER / RIGHT ─┐
                                                           ├─► state machine ──► TURN_LEFT / FORWARD / TURN_RIGHT / STOP
mic ──► "Golu" ──► speech to text ──► command matching ────┘         │
                                                                     └─► text to speech ──► speaker
```

**It works out of the box with no API keys**, in English, fully offline.
Adding two keys unlocks Hindi and barge-in.

## Two voice backends

| | LOCAL (default) | SARVAM |
| --- | --- | --- |
| API keys needed | none | Picovoice + Sarvam |
| Network | none, fully offline | yes |
| Languages | English | English, Hindi, Hinglish |
| Wake word | name spotted in the transcript | Porcupine, on-device |
| Barge-in | no | yes |
| "Golu, follow me" in one breath | yes | no, name first |

The backend is chosen automatically: Sarvam if both keys are present,
otherwise local. Force one with `--voice-backend local|sarvam`.

In local mode Whisper is **not** running continuously. Silero VAD gates it, so
an idle room costs nothing — Whisper only runs on audio that is actually
speech.

## Status

Tested on Windows 11, Python 3.14, Intel i5-1345U (CPU only).

| Part | State |
| --- | --- |
| Person detection + LEFT/CENTER/RIGHT | **working** — 99/99 frames live, ~16 fps |
| Robot state machine | **working** — 8/8 |
| Stale-box fix (`--detect-every`) | **working** — regression test passes |
| Command matching | **working** — 43/43, rejects "we should stop for lunch" |
| Local speech chain (VAD → Whisper → command) | **working** — 14/14 |
| Wake word "Golu" recognised | **working** — 7/7 pronunciations, all transcribe as "Golu" |
| Live mic → VAD → Whisper | **working** — transcribed real room speech; noise discarded |
| Wake-word gating against real conversation | **working** — real overheard speech, 0 false triggers |
| Repetition-loop rejection | **working** — a real 75x "Good luck." capture is discarded |
| Conversation flow rules | **working** — 10/10 |
| Keyboard control + clean exit | **working** |
| Graceful degradation with no keys | **working** |
| **Live "Golu, follow me" spoken by a human** | **untested** — needs you to say it |
| Sarvam STT / TTS, Porcupine, Hindi, barge-in | **untested** — no API keys available |

Everything in the local chain has been verified except a human actually
speaking the phrase, which no automated test can do.

## Run it

```powershell
.\.venv\Scripts\python.exe main.py
```

Then say:

> **"Golu, follow me"**

or say **"Golu"**, wait for it to answer *"Yes?"*, then say **"follow me"**.
To stop: **"Golu, stop"**.

A bare "follow me" with no name is deliberately ignored, so ordinary
conversation near the laptop does nothing.

Terminal output:

```
ROBOT STATE : FOLLOWING
VOICE STATE : LISTENING
PERSON      : CENTER
ACTION      : FORWARD
LAST HEARD  : Golu, follow me
```

### Keys (click the video window first)

| Key | What it does |
| --- | --- |
| `q` | quit cleanly |
| `f` | pretend you said "follow me" — works with no mic at all |
| `s` | pretend you said "stop" |

## Setup

```powershell
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

That is enough for English. For Hindi and barge-in, copy `.env.example` to
`.env` and add:

| Key | Where from |
| --- | --- |
| `PICOVOICE_ACCESS_KEY` | https://console.picovoice.ai (free) |
| `SARVAM_API_KEY` | https://dashboard.sarvam.ai |

**"Golu" is not one of Porcupine's built-in wake words** (those are
`porcupine`, `bumblebee`, `computer`, `jarvis`, `alexa`...). For the Sarvam
backend you must train a custom one at console.picovoice.ai, pick
**Windows (x86_64)**, and set `WAKE_WORD_PPN` to the downloaded `.ppn`.
The local backend has no such limit — it reads the name from the transcript,
so renaming the robot is one line in `.env`.

## Voice interaction states

```
SLEEPING ──"Golu"──► LISTENING ──you stop talking──► PROCESSING
    ▲                                                     │
    └──────────── reply finishes ◄──── SPEAKING ◄─────────┘
```

## How the listening actually works

The original version used a single adaptive loudness threshold and it was the
most fragile thing here: if the room was noisy while it calibrated, the
threshold landed above the user's voice and the microphone went deaf for the
whole session while looking perfectly healthy.

It now uses two stages:

1. A **fixed, low** loudness gate (0.004). It never calibrates, so it cannot
   be poisoned by background noise. It only asks "might something be
   happening?", cheaply.
2. **Silero VAD** then decides whether that audio is really speech and trims
   it. Fans, clicks and door bumps are discarded here — verified: silence and
   white noise are both rejected.

Only what survives both reaches Whisper. The gate can afford to be low
precisely because something smarter sits behind it.

### Teaching Whisper the robot's name

Whisper has never heard of "Golu", so left to itself it guesses — measured
outputs included `Hello`, `Galoo`, `Galu`, `Go look`, `Galo` and `Gullogue`.
Chasing those spellings is hopeless, and accepting `Hello` as the wake word
would have the robot answering every greeting in the room.

The fix is Whisper's own `initial_prompt`, which biases its vocabulary. We
hand it a sentence containing the name and the commands:

```
"Golu, follow me. Golu, stop. Hey Golu."
```

With that, **all 7 test pronunciations transcribe as literally "Golu"**, and
the small fast model becomes as accurate as the larger one — 740 ms instead
of 1490 ms per phrase. This is also why renaming the robot works: the prompt
is built from `WAKE_WORD`.

A short list of misspellings is still accepted as a fallback. `hello` and
`gala` are deliberately excluded: a missed wake is much better than a robot
that reacts to ordinary conversation.

### Repetition loops

Whisper sometimes degenerates and emits one phrase over and over — a real
capture here produced "Good luck." seventy-five times. Those transcripts are
detected and discarded, so the robot does not apologise at noise. Utterances
longer than 10 words with no wake word are also ignored in silence, because
they are somebody talking, not somebody giving an order.

## Barge-in — what is and isn't real

**Sarvam backend:** saying the wake word while the robot is talking cuts the
audio. This works because the robot never says its own name.

**Local backend:** no barge-in. The microphone is deliberately deafened while
the robot speaks.

**Neither does acoustic echo cancellation.** Interrupting by talking over the
robot without saying its name is not supported, and this prototype does not
fake it.

**Self-triggering is prevented in both** by disarming microphone capture while
the robot speaks, so it can never transcribe its own voice.

## The files

| File | What lives there |
| --- | --- |
| `main.py` | camera loop, terminal output, wiring |
| `vision.py` | camera, YOLO, LEFT/CENTER/RIGHT, box freshness, drawing |
| `behavior.py` | the robot state machine — pure functions |
| `voice.py` | conversation loop, both backends |
| `audio_in.py` | one microphone shared by everything |
| `local_stt.py` | offline VAD + Whisper (default) |
| `stt.py` | Sarvam realtime speech-to-text |
| `wakeword.py` | Porcupine (Sarvam backend only) |
| `tts.py` | Sarvam streaming voice, pyttsx3 fallback |
| `commands.py` | conservative command matching + wake-word splitting |
| `config.py` | reads `.env` |
| `speech.py` | **legacy**, unused — the old threshold-based input |

## Testing

None of these need keys or a network:

```powershell
.\.venv\Scripts\python.exe test_local_voice.py
```

```powershell
.\.venv\Scripts\python.exe test_voice_flow.py
```

```powershell
.\.venv\Scripts\python.exe test_commands.py
```

```powershell
.\.venv\Scripts\python.exe test_stale_box.py
```

```powershell
.\.venv\Scripts\python.exe selftest_pipeline.py
```

- `test_local_voice.py` — renders phrases with the Windows voice and pushes
  them through VAD → Whisper → matching. Covers everything but the mic.
- `test_voice_flow.py` — scripted conversations; proves a bare "follow me"
  while asleep is ignored.
- `selftest_voice.py` — eight staged live checks, mostly for the Sarvam path.
- `audio_in.py`, `local_stt.py` run standalone as their own live tests.

## When something breaks

**It never hears me.** Run `python audio_in.py` and make noise. If the level
stays near 0.00005 the mic is muted in Windows — there is often a dedicated
mic-mute key with an LED. That is not a code problem.

**It hears me but ignores me.** Run `main.py --debug-audio` to see every
transcript. If Whisper writes something other than "Golu", add that spelling
to `WAKE_VARIANTS` in `commands.py`. If it mishears the command, try
`--whisper-model base.en`.

**Detection is slow.** Live-camera measurements, whole loop:

| threads | imgsz | frame rate |
| --- | --- | --- |
| 2 | 320 | ~26 fps |
| 2 | 480 (default) | ~16 fps |
| 4 | 480 | ~11 fps |

`--threads` defaults to 2 on purpose: this is a hybrid Intel CPU and more
threads is *slower*.

## Useful options

| Option | Why |
| --- | --- |
| `--no-voice` | skip the mic entirely, use `f` / `s` |
| `--debug-audio` | print every transcript, including discarded noise |
| `--voice-backend local` | force offline mode even if keys exist |
| `--whisper-model base.en` | more accurate, roughly twice as slow |
| `--camera 1` | if the wrong camera opens |
| `--imgsz 320` | faster detection |
| `--seconds 30` | quit automatically, for unattended testing |

## Known limits

- One person only — it follows the largest box in the frame.
- No distance sensing, so `FORWARD` doesn't know how far away you are.
- Local mode is English only. Hindi needs the Sarvam backend.
- Local mode has no barge-in.
- Two commands only. Add more in `commands.py` and `behavior.py`.
- Roughly 1.5–2 s from finishing a sentence to the action changing: 0.7 s to
  notice you stopped, then Whisper.
- Command matching is a fixed phrase list, so unanticipated phrasing will not
  be understood. That is deliberate.
