"""
main.py
-------
Companion robot brain, v0 - laptop prototype.

    webcam ---> vision.py ---> person? LEFT/CENTER/RIGHT --.
                                                            >--> behavior.py --> action
    mic ---> wakeword ---> stt ---> commands --------------'          |
             ("Milo")     (Sarvam)  (follow/stop)                     '--> tts.py --> speaker

The camera loop below never waits for the microphone, the network or the
speaker. All of that lives on other threads (see voice.py) and reaches this
loop through a callback and a state label.

Run it:
    python main.py

Keys (click the video window first):
    q  quit
    f  pretend you said "follow me"   (handy if the mic is misbehaving)
    s  pretend you said "stop"
"""

import argparse
import queue
import sys
import time

import cv2

import audio_in
import behavior
import commands
import config
import stt
import tts
import vision
import voice
import wakeword


def parse_args():
    p = argparse.ArgumentParser(description="Companion robot brain v0")
    p.add_argument("--camera", type=int, default=0,
                   help="webcam index (0 is the built-in camera)")
    p.add_argument("--mic", type=int, default=None,
                   help="microphone device index; default = Windows default mic")
    p.add_argument("--model", default="yolo11n.pt",
                   help="YOLO weights; nano is the fastest")
    p.add_argument("--conf", type=float, default=0.4,
                   help="detection confidence threshold")
    p.add_argument("--imgsz", type=int, default=480,
                   help="YOLO input size; smaller = faster, less accurate")
    p.add_argument("--center-band", type=float, default=0.34,
                   help="width of the CENTER zone as a fraction of the frame")
    p.add_argument("--threads", type=int, default=2,
                   help="CPU threads for YOLO; 2 is fastest on this hybrid CPU")
    p.add_argument("--detect-every", type=int, default=1,
                   help="run the detector every N frames (2 nearly doubles the frame rate)")
    p.add_argument("--hold", type=float, default=0.3,
                   help="keep the last box this long when detection flickers (seconds)")
    p.add_argument("--no-voice", action="store_true",
                   help="skip the microphone entirely and use the f / s keys")
    p.add_argument("--no-mirror", action="store_true",
                   help="show the raw camera view instead of mirroring it")
    p.add_argument("--debug-audio", action="store_true",
                   help="print every transcript, including discarded noise")
    p.add_argument("--voice-backend", choices=["local", "sarvam"], default=None,
                   help="force a backend. Default: sarvam if both API keys are "
                        "set, otherwise the offline English one.")
    p.add_argument("--whisper-model", default="tiny.en",
                   help="offline model size. tiny.en (740ms) matches base.en "
                        "(1490ms) for accuracy here once the wake word is fed "
                        "to Whisper as an initial_prompt, so it is the default. "
                        "Try base.en if it mishears your commands.")
    # The two below are for testing / running without a desktop session.
    p.add_argument("--seconds", type=float, default=None,
                   help="quit automatically after this many seconds")
    p.add_argument("--no-window", action="store_true",
                   help="don't open the video window; terminal output only")
    return p.parse_args()


# Recognised commands are handed from the voice thread to the camera loop
# through this queue. A queue rather than a shared variable so nothing is
# ever lost or half-written between threads.
_voice_commands = queue.Queue()


def print_status(robot_state, voice_state, position, action, last_heard):
    """
    Print the live status block.

    Only called when something actually changed, otherwise this would scroll
    past 16 times a second and be unreadable.
    """
    print(f"ROBOT STATE : {robot_state}")
    print(f"VOICE STATE : {voice_state}")
    print(f"PERSON      : {position if position else 'NO PERSON'}")
    print(f"ACTION      : {action}")
    print(f"LAST HEARD  : {last_heard if last_heard else '-'}")
    print("-" * 46, flush=True)


def main():
    args = parse_args()

    # Thread count matters a lot here, and not in the obvious direction.
    # This is a hybrid Intel CPU (a few fast P-cores + many slow E-cores), and
    # spreading the work onto the slow cores makes it worse.
    #
    # Measured on live camera frames, whole loop (camera read + detection):
    #     2 threads, imgsz 320 -> 26 fps
    #     2 threads, imgsz 480 -> 16 fps   (the defaults)
    #     4 threads, imgsz 480 -> 11 fps
    # So we keep the thread count small. It also leaves CPU free for Whisper.
    # Run with --imgsz 320 if you want a smoother picture.
    try:
        import torch
        torch.set_num_threads(args.threads)
    except Exception:
        pass

    # --- camera ---------------------------------------------------------
    print(f"[main] Opening camera {args.camera}...")
    cap = vision.open_camera(args.camera)
    if cap is None:
        print(f"[main] ERROR: could not open camera {args.camera}.")
        print("       Close Teams/Zoom/Camera app, or try --camera 1.")
        print("       Also check Settings > Privacy & security > Camera.")
        return 1

    # A blocked camera still returns frames, just a blank privacy card, so
    # warn about it here rather than letting it look like a detection bug.
    ok, probe = cap.read()
    if ok and vision.looks_blocked(probe):
        print("[main] WARNING: the camera is returning a blank/placeholder image.")
        print("       The lens is probably covered by the privacy shutter, or the")
        print("       camera is switched off in your laptop's privacy settings.")
        print("       Nothing will ever be detected until that is turned on.")

    # --- detector -------------------------------------------------------
    print(f"[main] Loading detector {args.model} (downloads once, ~5 MB)...")
    try:
        model = vision.load_detector(args.model)
    except Exception as exc:
        print(f"[main] ERROR: could not load the YOLO model: {exc}")
        cap.release()
        return 1

    # --- speaker --------------------------------------------------------
    tts.start()

    # --- voice: microphone, wake word, speech recognition ----------------
    # Each stage degrades on its own. A missing key or a muted mic disables
    # voice but must never stop the camera loop or the f / s keys.
    voice_on = False
    if not args.no_voice:
        for line in config.describe():
            print(f"[cfg]  {line}")

        mic_ok = audio_in.start(device=args.mic if args.mic is not None
                                else config.MIC_DEVICE,
                                blocksize=512)
        if not mic_ok:
            print("[main] Microphone unavailable - carrying on with the f / s keys.")
        else:
            voice_on = voice.start(
                on_command=lambda c, t: _voice_commands.put((c, t)),
                prefer=args.voice_backend,
                whisper_model=args.whisper_model,
                debug=args.debug_audio)
        if not voice_on:
            print("[main] Voice control is off. The f / s keys still work.")
    else:
        print("[main] Voice disabled (--no-voice). Use the f / s keys.")

    print()
    print("=" * 46)
    if voice_on and voice.backend() == "local":
        # Offline mode spots the name in the transcript, so the name and the
        # command can arrive in one breath.
        print(f" Say: \"{config.WAKE_WORD}, follow me\"   or   \"{config.WAKE_WORD}, stop\"")
        print(f" (or just \"{config.WAKE_WORD}\", wait for 'Yes?', then speak)")
        print(" English only in this mode.")
    elif voice_on:
        wake_name = wakeword.active_name() or config.WAKE_WORD
        print(f" Say '{wake_name}', then 'follow me' / 'stop'")
        print(" or in Hindi: 'mere saath chalo' / 'ruko'")
    else:
        print(" Press f = follow me, s = stop.")
    print(" Press q in the video window to quit.")
    print("=" * 46)
    print()

    # --- state ----------------------------------------------------------
    state = behavior.IDLE
    action = behavior.STOP
    last_command = None
    last_heard = None
    last_printed = None          # so we only print when something changes
    last_box = None              # last good detection, for the flicker hold
    last_box_time = 0.0
    frame_index = 0
    failed_reads = 0
    fps_smoothed = 0.0
    prev_time = time.time()
    loop_start = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                # A dropped frame now and then is normal; a run of them is not.
                failed_reads += 1
                if failed_reads > 30:
                    print("[main] ERROR: lost the camera feed. Exiting.")
                    break
                continue
            failed_reads = 0

            # Mirror so the preview behaves like a mirror: when you step to
            # your left, the box moves to the left of the window too.
            if not args.no_mirror:
                frame = cv2.flip(frame, 1)

            height, width = frame.shape[:2]
            frame_index += 1

            # --- 1. vision: where is the person? ---
            frame_time = time.time()
            if frame_index % args.detect_every == 0:
                detected = vision.detect_person(model, frame, conf=args.conf,
                                                imgsz=args.imgsz)
                if detected is not None:
                    last_box, last_box_time = detected, frame_time

            # One rule, applied on every frame - detector frames and skipped
            # frames alike: only use the stored box while it is still fresh.
            # See vision.fresh_box for why this is centralised.
            box = vision.fresh_box(last_box, last_box_time, frame_time, args.hold)

            position = vision.classify_position(box, width, args.center_band)

            # --- 2. hearing: did the voice thread recognise a command? ---
            # Non-blocking: if nothing is waiting we carry straight on.
            command = None
            from_keyboard = False
            try:
                command, heard_text = _voice_commands.get_nowait()
                last_command, last_heard = command, heard_text
            except queue.Empty:
                pass

            # --- 3. keyboard shortcuts (also the quit key) ---
            # waitKey only works while a window exists, so skip it in --no-window.
            key = (cv2.waitKey(1) & 0xFF) if not args.no_window else 255
            if key == ord("q"):
                break
            elif key == ord("f"):
                command, from_keyboard = behavior.CMD_FOLLOW_ME, True
                last_command, last_heard = command, "follow me (key)"
            elif key == ord("s"):
                command, from_keyboard = behavior.CMD_STOP, True
                last_command, last_heard = command, "stop (key)"

            # --- 4. brain: decide state + action ---
            state, action, say_text = behavior.decide(state, command, position)

            # Only speak here for keyboard commands. When the command came by
            # voice, voice.py has already spoken the reply - and it picks
            # Hindi or English to match what you said, which this cannot.
            if say_text and from_keyboard:
                tts.say(say_text)

            # --- 5. terminal output, only when something changed ---
            voice_state = voice.state() if voice_on else "OFF"
            snapshot = (state, voice_state, position, action, last_heard)
            if snapshot != last_printed:
                print_status(state, voice_state, position, action, last_heard)
                last_printed = snapshot

            # --- 6. the video window ---
            now = time.time()
            fps = 1.0 / max(now - prev_time, 1e-6)
            prev_time = now
            fps_smoothed = fps if fps_smoothed == 0 else 0.9 * fps_smoothed + 0.1 * fps

            if not args.no_window:
                # The overlay only renders ASCII, so pass the command name
                # rather than a Hindi transcript, which OpenCV cannot draw.
                vision.draw_overlay(frame, box, position, state, action,
                                    last_command, args.center_band)
                cv2.putText(frame, f"VOICE: {voice_state}",
                            (10, height - 56), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (0, 220, 220), 1, cv2.LINE_AA)
                cv2.putText(frame, f"{fps_smoothed:4.1f} fps  |  q=quit  f=follow  s=stop",
                            (10, height - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (200, 200, 200), 1, cv2.LINE_AA)
                cv2.imshow("companion_v0", frame)

                # If the user closes the window with the X button, stop cleanly.
                if cv2.getWindowProperty("companion_v0", cv2.WND_PROP_VISIBLE) < 1:
                    break

            # --- 7. optional auto-quit, used by the smoke test ---
            if args.seconds is not None and time.time() - loop_start > args.seconds:
                print(f"[main] --seconds {args.seconds} elapsed, stopping.")
                break

    except KeyboardInterrupt:
        print("\n[main] Ctrl+C - shutting down.")
    finally:
        # Always release the hardware, even after an error, or the camera
        # stays locked and the next run fails to open it.
        # Shut down in the reverse order things were started, and never let
        # one failure stop the rest from being cleaned up.
        for label, fn in (("camera", cap.release),
                          ("windows", cv2.destroyAllWindows),
                          ("voice", voice.stop),
                          ("wake word", wakeword.shutdown),
                          ("microphone", audio_in.stop),
                          ("speaker", tts.shutdown)):
            try:
                fn()
            except Exception as exc:
                print(f"[main] problem shutting down {label}: {exc}")
        print("[main] Bye.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
