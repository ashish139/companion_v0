# Verification handoff — companion_v0

For an independent reviewer (human or another AI). **Please be skeptical.**
The goal is to find what is wrong, overstated or fragile — not to confirm it.

Source: **https://github.com/ashish139/companion_v0**
Read the code. Do not assume the claims below are true.

---

## 1. What it is

A laptop prototype of a "companion robot brain". No robot exists; movement
commands are printed. Intended eventually for a Pollen Robotics MicroDuck.

```
webcam ──► YOLO11n ──► LEFT / CENTER / RIGHT ─┐
                                              ├─► state machine ──► TURN_LEFT / FORWARD / TURN_RIGHT / STOP
mic ──► "Golu" ──► speech to text ──► command matching ─┘   │
                                                            └─► text to speech ──► speaker
```

Requirements it was built against: Windows, Python 3.11+, OpenCV, off-the-shelf
detector, wake word, local-first speech, no ROS, no depth camera, no robot SDK,
no RL, no paid services. Readable for a data scientist, not a software
engineer. Speech must never block the camera loop. Must not repeat spoken
messages every frame. Must degrade gracefully when hardware or keys are absent.

## 2. Two voice backends

| | LOCAL (default, active) | SARVAM (never run) |
| --- | --- | --- |
| Keys | none | Picovoice + Sarvam |
| Languages | English | + Hindi, Hinglish |
| Wake word | name found in the transcript | Porcupine on-device |
| Barge-in | no | yes |

**The machine has neither API key set**, so the entire Sarvam/Porcupine path
is written but has never executed. Treat it as unverified code.

## 3. Environment

Windows 11, Python 3.14.4, Intel i5-1345U (hybrid 2P+8E), 32 GB, Iris Xe
(unused — all inference on CPU).

## 4. Claims, and the evidence offered

Check whether the code actually supports each.

| Claim | Evidence |
| --- | --- |
| State machine correct | `python behavior.py` → 8/8 |
| Vision → action correct | `python selftest_pipeline.py` → 4/4 |
| Live detection | 99/99 frames on a live webcam, ~16 fps |
| Command matching | `python test_commands.py` → 46/46 |
| Full local speech chain | `python test_local_voice.py` → 14/14 |
| Wake word recognised | `python test_wake_variants.py` → 7/7 pronunciations |
| Conversation rules | `python test_voice_flow.py` → 10/10 |
| Stale-box regression | `python test_stale_box.py` → passes |
| Live mic → VAD → Whisper | real room speech transcribed; noise discarded |
| Wake gating vs real speech | real overheard conversation, 0 false triggers |

## 5. NOT verified

* **No human has ever spoken "Golu, follow me" into it.** Every link is tested
  in isolation; the end-to-end path with a real voice is untested.
* The entire Sarvam backend, Porcupine, Hindi, and barge-in.
* One live run showed a false wake from background audio. Two-word wake
  variants were removed in response, but the fix is unverified.

## 6. Please scrutinise these

1. **`local_stt.py` two-stage gate.** A fixed RMS gate (0.004, never adaptive)
   followed by Silero VAD. Is the fixed gate too low in a loud room, and does
   Silero reliably reject what gets through? Is `MAX_UTTERANCE = 8.0`
   exploitable by continuous noise?
2. **`initial_prompt` bias.** The wake word and both commands are fed to
   Whisper as a prompt to make it spell "Golu" correctly. Does this bias
   Whisper toward *hallucinating* those words in unrelated audio? That would
   cause false commands, which is the worst failure mode here.
3. **`looks_degenerate()`** in `local_stt.py`. Does it ever discard a real
   command? Check the 60% / 4-repeat thresholds.
4. **Self-hearing.** `voice._speak_and_wait` disarms mic capture while the
   robot talks, then drains and re-arms. Is there a race where audio recorded
   just before disarming is transcribed afterwards?
5. **Wake-word filler.** `commands.py` adds the wake word and variants to the
   filler set. Does that widen what counts as a command too far?
6. **Thread shutdown.** Three threads (main, voice, tts) plus an audio reader,
   several queues. Check `main.py`'s `finally` block for hangs or leaks,
   especially if the camera fails mid-loop.
7. **`stt.py` was written blind.** Signatures came from introspecting the
   installed SDK, but no event payload has ever been observed. Most likely
   thing to break on first real use.

## 8. Deliberate choices that may look like bugs

* `--threads` defaults to 2. Measured: 2 threads 16 fps, 4 threads 11 fps on
  this hybrid CPU. More is slower.
* The video frame is mirrored by default (`--no-mirror` disables).
* `looks_blocked()` flags a shuttered camera, which returns a flat grey
  placeholder rather than failing to open. This cost hours once.
* `"stop"` is checked before `"follow"`, so "stop following me" means stop.
* `"hello"` and `"gala"` are excluded from wake variants even though Whisper
  produces them for "Golu" — too common; a missed wake beats a false one.
* `decide()` returns `say_text=None` on almost every call. That is what stops
  the robot talking on all 16 frames per second.
* `speech.py` is dead code, retained and labelled.

## 9. Reproducing

No camera, mic, keys or network needed:

```
python behavior.py
python test_commands.py
python test_stale_box.py
python test_voice_flow.py
python test_local_voice.py
python selftest_pipeline.py
```

Need hardware: `selftest_vision.py` (camera + a person), `audio_in.py` (mic),
`test_wake_variants.py` (loads Whisper), `main.py`.

## 10. What would help most

1. Does the code meet section 1? Anything silently missed?
2. Are any of the seven concerns in section 6 real defects? Give a concrete
   failing scenario — inputs and state, not a general worry.
3. Is any claim in section 4 overstated relative to what the tests show?
4. Anything unsafe, leaking, or liable to hang?

Cite file and line. If a claim looks unsupported, say so plainly.
