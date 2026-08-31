"""
End-to-end check with no camera and no microphone needed.

It takes a real photo of a person, pastes them on the left, in the centre and
on the right of a fake 640x480 "camera frame", and pushes each frame through
the real detector, the real zone logic and the real state machine.

That covers the one thing a live camera test can't: proving the whole chain
produces the right movement for each position, on demand.

    python selftest_pipeline.py
"""

import os

import cv2
import numpy as np

import behavior
import vision

FRAME_W, FRAME_H = 640, 480
PHOTO = "bus.jpg"  # ships with Ultralytics; downloaded once if missing


def get_person_cutout():
    """Download a stock photo and crop out one person."""
    if not os.path.exists(PHOTO):
        from ultralytics.utils.downloads import safe_download
        safe_download("https://ultralytics.com/images/bus.jpg", file=PHOTO)

    img = cv2.imread(PHOTO)
    if img is None:
        raise SystemExit("could not read the test photo")

    model = vision.load_detector("yolo11n.pt")
    box = vision.detect_person(model, img, conf=0.4, imgsz=640)
    if box is None:
        raise SystemExit("no person found in the test photo")

    x1, y1, x2, y2 = box
    cutout = img[y1:y2, x1:x2]
    # Scale so the person is a realistic height for a 480px-tall frame.
    scale = (FRAME_H * 0.8) / cutout.shape[0]
    return model, cv2.resize(cutout, (int(cutout.shape[1] * scale),
                                      int(cutout.shape[0] * scale)))


def frame_with_person_at(cutout, cx):
    """Paste the person onto a plain background with their centre at x=cx."""
    frame = np.full((FRAME_H, FRAME_W, 3), 130, dtype=np.uint8)
    h, w = cutout.shape[:2]
    x = int(cx - w / 2)
    y = FRAME_H - h
    x = max(0, min(x, FRAME_W - w))
    frame[y:y + h, x:x + w] = cutout
    return frame


def main():
    model, cutout = get_person_cutout()

    # (label, x centre, expected position, expected action while FOLLOWING)
    cases = [
        ("far left",  90,  "LEFT",   behavior.TURN_LEFT),
        ("centre",    320, "CENTER", behavior.FORWARD),
        ("far right", 550, "RIGHT",  behavior.TURN_RIGHT),
    ]

    print("--- while FOLLOWING, with a real person detected ---")
    passed = 0
    for label, cx, want_pos, want_action in cases:
        frame = frame_with_person_at(cutout, cx)
        box = vision.detect_person(model, frame, conf=0.4, imgsz=480)
        pos = vision.classify_position(box, FRAME_W)
        state, action, _ = behavior.decide(behavior.FOLLOWING, None, pos)
        ok = (pos == want_pos and action == want_action)
        passed += ok
        print(f"[{'ok ' if ok else 'FAIL'}] person {label:9} -> detected={box is not None} "
              f"PERSON={pos}  ACTION={action}  (want {want_pos}/{want_action})")

    print("\n--- empty frame: nothing to follow ---")
    empty = np.full((FRAME_H, FRAME_W, 3), 130, dtype=np.uint8)
    box = vision.detect_person(model, empty, conf=0.4, imgsz=480)
    pos = vision.classify_position(box, FRAME_W)
    _, action, _ = behavior.decide(behavior.FOLLOWING, None, pos)
    ok = (box is None and action == behavior.STOP)
    passed += ok
    print(f"[{'ok ' if ok else 'FAIL'}] no person -> detected={box is not None} "
          f"ACTION={action}  (want STOP)")

    print("\n--- the spoken demo, step by step ---")
    script = [
        ("you say 'follow me'", behavior.CMD_FOLLOW_ME, "LEFT"),
        ("you move to centre",  None,                   "CENTER"),
        ("you say 'stop'",      behavior.CMD_STOP,      "CENTER"),
    ]
    state = behavior.IDLE
    for label, cmd, pos in script:
        state, action, say = behavior.decide(state, cmd, pos)
        print(f"  {label:22} -> STATE: {state:9} PERSON: {pos:6} ACTION: {action:10}"
              + (f'  robot says: "{say}"' if say else ""))

    total = len(cases) + 1
    print(f"\n{passed}/{total} vision checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
