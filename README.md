# companion_v0 — companion robot brain (laptop prototype)

A laptop-only demo of the "brain" a following robot would need. There is no
robot: the movement commands are printed instead of driven.

```
webcam ──► YOLO person detector ──► LEFT / CENTER / RIGHT ─┐
                                                           ├─► state machine ──► TURN_LEFT / FORWARD / TURN_RIGHT / STOP
mic ──► "Milo" ──► Sarvam STT ──► command matching ────────┘         │
        (local)    (Hindi/English/Hinglish)                          └─► Sarvam TTS ──► speaker
```

The vision half runs entirely on your CPU. The voice half is wake-word-gated:
nothing is sent anywhere until you say the robot's name.

## Status

Tested on Windows 11, Python 3.14, Intel i5-1345U (CPU only).

| Part | State |
| --- | --- |
| Person detection + LEFT/CENTER/RIGHT | **working** — 99/99 frames live, ~16 fps |
| Robot state machine | **working** — 8/8 cases |
| Stale-box fix (`--detect-every`) | **working** — regression test passes |
| Command matching EN/HI/Hinglish | **working** — 43/43 including false-positive rejection |
| Keyboard control + clean exit | **working** — verified with the real window |
| Graceful degradation with no keys | **working** — verified |
| Wake word ("Milo") | **untested** — needs a Picovoice AccessKey |
| Sarvam speech-to-text | **untested** — needs a Sarvam API key |
| Sarvam text-to-speech | **untested** — needs a Sarvam API key |
| Offline fallback voice (pyttsx3) | **working** |

Nothing that needs an API key has ever been run. Use `selftest_voice.py` to
check each stage once you have keys.

## Setup

```powershell
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then copy `.env.example` to `.env` and fill in two keys:

| Key | Where from | Free? |
| --- | --- | --- |
| `PICOVOICE_ACCESS_KEY` | https://console.picovoice.ai | yes |
| `SARVAM_API_KEY` | https://dashboard.sarvam.ai | has a free tier |

### Making it actually answer to "Milo"

**"Milo" is not one of Porcupine's built-in wake words.** The built-ins are
`porcupine`, `bumblebee`, `computer`, `jarvis`, `alexa` and a handful of
others. Until you train a custom one, the app falls back to `computer` and
tells you so at startup.

To get the real name:

1. Go to https://console.picovoice.ai → Porcupine
2. Type `Milo`, pick **Windows (x86_64)**, train (takes seconds), download
3. Unzip and set `WAKE_WORD_PPN=C:\path\to\Milo_en_windows_v3_0_0.ppn` in `.env`

To rename the robot later, train a new word and change that one line.

## Run it

```powershell
.\.venv\Scripts\python.exe main.py
```

Say **"Milo"**, wait for `VOICE STATE : LISTENING`, then say a command:

| English | Hindi | Hinglish |
| --- | --- | --- |
| "follow me" | "मेरे साथ चलो" / "मेरे पीछे आओ" | "mere saath chalo" |
| "stop" | "रुको" / "रुक जाओ" | "ruk jao" |

Terminal output:

```
ROBOT STATE : FOLLOWING
VOICE STATE : LISTENING
PERSON      : CENTER
ACTION      : FORWARD
LAST HEARD  : मेरे साथ चलो
```

### Keys (click the video window first)

| Key | What it does |
| --- | --- |
| `q` | quit cleanly |
| `f` | pretend you said "follow me" — works with no keys or mic at all |
| `s` | pretend you said "stop" |

## Voice interaction states

```
SLEEPING ──"Milo"──► LISTENING ──you stop talking──► PROCESSING
    ▲                                                     │
    └──────────── reply finishes ◄──── SPEAKING ◄─────────┘
```

Whisper is **not** running continuously. While SLEEPING, the only thing
listening is Porcupine, which is a tiny on-device model. Audio only leaves
your laptop after the wake word.

## Barge-in — what is and isn't real here

**Implemented:** saying the wake word while the robot is talking cuts the
audio and starts listening again. This works because the robot never says its
own name, so it cannot interrupt itself.

**Not implemented:** interrupting on *any* speech. That needs acoustic echo
cancellation to separate your voice from the robot's own output coming back
through the microphone, and this prototype does not do AEC. Speaking over the
robot without saying "Milo" will not stop it.

**Self-triggering is prevented** by construction: the speech recogniser is
only fed audio in the LISTENING state, so while the robot is speaking there
is nothing transcribing its output.

(Note: this particular laptop's Intel Smart Sound mic array appears to do
hardware echo cancellation — measured speaker output arriving at the mic at
0.00006 RMS. That helps, but it is a property of this hardware, not of this
code, so do not rely on it.)

## The files

| File | What lives there |
| --- | --- |
| `main.py` | camera loop, terminal output, wiring |
| `vision.py` | camera, YOLO, LEFT/CENTER/RIGHT, box freshness, drawing |
| `behavior.py` | the robot state machine — pure functions, unchanged |
| `voice.py` | SLEEPING/LISTENING/PROCESSING/SPEAKING orchestration |
| `audio_in.py` | one microphone shared by the wake word and the recogniser |
| `wakeword.py` | Porcupine |
| `stt.py` | Sarvam realtime speech-to-text |
| `tts.py` | Sarvam streaming voice, pyttsx3 fallback |
| `commands.py` | conservative EN/HI/Hinglish command matching |
| `config.py` | reads `.env` |
| `speech.py` | **legacy**, unused — the old local-Whisper input |

## Testing

```powershell
.\.venv\Scripts\python.exe selftest_voice.py
```

Runs eight stages in the order things break, and tells you which one failed:
config → microphone → wake word → STT English → STT Hindi → TTS English →
TTS Hindi → command matching. Run a single stage with e.g.
`selftest_voice.py 3`.

Others, none of which need keys or a network:

```powershell
.\.venv\Scripts\python.exe test_commands.py
```

```powershell
.\.venv\Scripts\python.exe test_stale_box.py
```

```powershell
.\.venv\Scripts\python.exe selftest_pipeline.py
```

```powershell
.\.venv\Scripts\python.exe behavior.py
```

`selftest_vision.py` needs the camera and a person in frame. `audio_in.py`,
`wakeword.py`, `stt.py` and `tts.py` each run standalone as their own test.

## When something breaks

**"could not open camera 0"** — Teams, Zoom or the Camera app is holding it,
or the privacy shutter is closed. The app warns you if the camera is
returning a blank privacy placeholder.

**Wake word never fires** — run `selftest_voice.py 2` first. If the mic is
silent, it is muted in Windows (there is often a dedicated mic-mute key with
an LED), not a code problem. If the mic is fine, raise `WAKE_SENSITIVITY`
toward 1.0 in `.env`.

**It wakes but understands nothing** — `selftest_voice.py 4`. Usually a
missing or expired `SARVAM_API_KEY`, or no network.

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
| `--debug-audio` | print wake-word hits and partial transcripts |
| `--camera 1` | if the wrong camera opens |
| `--mic 5` | pick a specific microphone |
| `--imgsz 320` | faster detection |
| `--detect-every 2` | detect on every other frame |
| `--no-mirror` | raw camera view instead of mirrored |
| `--center-band 0.5` | wider CENTER zone, so FORWARD triggers more |
| `--seconds 30` | quit automatically, for unattended testing |

## Known limits

- One person only — it follows the largest box in the frame.
- No distance sensing, so `FORWARD` doesn't know how far away you are.
- Two commands only. Add more in `commands.py` and give them a rule in
  `behavior.py`.
- A new WebSocket is opened per utterance, which costs a few hundred
  milliseconds. A persistent connection would be faster but harder to read.
- Command matching is a fixed phrase list, so a phrasing nobody thought of
  will not be understood. That is deliberate — loose matching used to fire
  on "we should stop for lunch".
- Sarvam calls send your audio to Sarvam's servers. Only after the wake word,
  but it is not local.
