# companion_v0 — companion robot brain (laptop prototype)

A laptop-only demo of the "brain" a following robot would need. There is no
robot: the movement commands are printed instead of driven.

```
webcam ──► YOLO person detector ──► LEFT / CENTER / RIGHT ─┐
                                                           ├─► state machine ──► TURN_LEFT / FORWARD / TURN_RIGHT / STOP
microphone ──► Whisper ──► "follow me" / "stop" ───────────┘         │
                                                                     └─► pyttsx3 ──► speaker
```

Everything runs locally on the CPU. No cloud services, no ROS, no depth
camera, no robot SDK.

## Status

Work in progress. Tested on Windows 11, Python 3.14, Intel i5-1345U (CPU only).

| Part | State |
| --- | --- |
| Person detection + LEFT/CENTER/RIGHT | working — 99/99 frames on a live webcam, ~16 fps |
| State machine | working — 8/8 cases, run `python behavior.py` |
| Text-to-speech | working |
| Microphone capture + Whisper | working — transcribes accurately |
| Voice command end-to-end | **not yet confirmed** on a live human voice |

The voice chain has been verified piece by piece (6/6 on rendered speech,
0 false triggers on 10 unrelated utterances) but never captured a successful
live "follow me" in one sitting. Use `--no-voice` and the `f` / `s` keys if
the microphone gives you trouble.

## Run it

```powershell
cd companion_v0; .\.venv\Scripts\python.exe main.py
```

Or activate the environment first, then just use `python`:

```powershell
cd companion_v0; .\.venv\Scripts\Activate.ps1
```

```powershell
python main.py
```

### Keys (click the video window first)

| Key | What it does |
| --- | --- |
| `q` | quit cleanly |
| `f` | pretend you said "follow me" — useful if the mic misbehaves |
| `s` | pretend you said "stop" |

### What you should see

Say **"follow me"**, the laptop says *"Okay, following you."* and the terminal
prints a new block every time something changes:

```
STATE:  FOLLOWING
PERSON: LEFT
ACTION: TURN_LEFT
HEARD:  follow me
```

Step into the middle of the frame and it becomes `PERSON: CENTER`,
`ACTION: FORWARD`. Say **"stop"**, it says *"Stopping."* and drops to
`STATE: STOPPED`, `ACTION: STOP`.

The video window shows your bounding box, the two lines dividing
LEFT / CENTER / RIGHT, and the same status text.

## The files

| File | What lives there |
| --- | --- |
| `main.py` | the loop that ties everything together, plus the terminal output |
| `vision.py` | camera, YOLO, LEFT/CENTER/RIGHT, drawing the overlay |
| `speech.py` | microphone, Whisper, matching "follow me" / "stop" |
| `behavior.py` | the state machine — plain functions, no side effects |
| `tts.py` | speaking, on its own thread so the video never freezes |
| `selftest_*.py` | checks you can run when something breaks (see below) |

## Useful options

| Option | Why you'd use it |
| --- | --- |
| `--no-voice` | skip the mic entirely and drive it with the `f` / `s` keys |
| `--debug-audio` | print a mic level meter and everything Whisper hears |
| `--mic-threshold 0.002` | if it never hears you, lower this; if it triggers on noise, raise it |
| `--whisper-model base.en` | more accurate than the default `tiny.en`, about twice as slow |
| `--camera 1` | if the wrong camera opens |
| `--mic 5` | pick a specific microphone (see the list `speech.py` can print) |
| `--imgsz 320` | faster detection on a busy machine |
| `--detect-every 2` | run the detector on every other frame; nearly doubles the frame rate |
| `--no-mirror` | show the raw camera view instead of a mirrored one |
| `--center-band 0.5` | make the CENTER zone wider, so it says FORWARD more often |
| `--seconds 30` | quit automatically, for unattended testing |

## When something breaks

Each piece can be tested on its own:

```powershell
.\.venv\Scripts\python.exe behavior.py
```

```powershell
.\.venv\Scripts\python.exe tts.py
```

```powershell
.\.venv\Scripts\python.exe speech.py
```

```powershell
.\.venv\Scripts\python.exe selftest_vision.py
```

```powershell
.\.venv\Scripts\python.exe selftest_pipeline.py
```

- `behavior.py` — prints 8 state-machine cases, all should say `ok`.
- `tts.py` — you should hear two sentences.
- `speech.py` — live mic test. Prints a level meter once a second and every
  transcript. **Speak and watch the level rise.** Pass a number to override the
  threshold, e.g. `python speech.py 0.002`.
- `selftest_vision.py` — 12 seconds of real webcam detection, reports the frame
  rate and how often it saw you. Stand in front of the camera.
- `selftest_pipeline.py` — no camera or mic needed. Pastes a real photo of a
  person at three positions and checks the whole chain produces
  TURN_LEFT / FORWARD / TURN_RIGHT.

### Common problems

**"could not open camera 0"** — Teams, Zoom or the Camera app is holding it.
Close them, or try `--camera 1`. Also check
Settings → Privacy & security → Camera.

**It never hears me.** Run `python speech.py` and watch the level meter while
you talk. If the numbers stay tiny, the mic is muted in Windows or the wrong
device is selected — try `--mic 5`. If the numbers rise but no command fires,
lower the threshold: `--mic-threshold 0.002`.

**It hears itself.** It shouldn't: the mic is muted while the robot is
speaking. This laptop's Intel Smart Sound mic array also does hardware echo
cancellation, which helps.

**Detection is slow.** Measured on this laptop with the live camera, for the
whole loop (grab a frame + detect):

| threads | imgsz | frame rate |
| --- | --- | --- |
| 2 | 320 | ~26 fps |
| 2 | 480 (default) | ~16 fps |
| 4 | 480 | ~11 fps |

So use `--imgsz 320` for a smoother picture, or `--detect-every 2`. Note that
`--threads` defaults to 2 on purpose: this is a hybrid Intel CPU (fast P-cores
plus slow E-cores) and using *more* threads is slower, not faster. Expect
run-to-run variation of a few fps depending on how warm the machine is.

## Setting it up again from scratch

```powershell
git clone https://github.com/ashish139/companion_v0.git; cd companion_v0; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The YOLO weights (~5 MB) and the Whisper model (~75 MB) download themselves on
first run and are then cached.

## Known limits

- One person only — it follows the largest box in the frame.
- No distance sensing, so `FORWARD` doesn't know how far away you are.
- Roughly 1.5 s from finishing a word to the action changing: 0.6 s to notice
  you stopped talking, ~0.9 s for Whisper.
- Only two commands. Add more in `match_command()` in `speech.py` and give them
  a rule in `behavior.py`.
