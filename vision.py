"""
vision.py
---------
Everything camera-related:

    webcam frame  ->  YOLO person detector  ->  bounding box  ->  LEFT / CENTER / RIGHT

Nothing in here knows about the state machine, the microphone or the speaker.
It just answers two questions per frame:
    1. Is there a person?
    2. Which horizontal third of the frame are they standing in?
"""

import cv2
from ultralytics import YOLO

# In the COCO dataset that YOLO is pretrained on, class id 0 is "person".
PERSON_CLASS_ID = 0

# Colours are BGR (OpenCV's order), not RGB.
GREEN = (0, 200, 0)
GREY = (110, 110, 110)
WHITE = (255, 255, 255)
YELLOW = (0, 220, 220)
RED = (0, 0, 230)


def open_camera(index=0, width=640, height=480):
    """
    Open the webcam and return a cv2.VideoCapture, or None if it failed.

    We use CAP_DSHOW (DirectShow) because on Windows the default backend can
    take several seconds to open the camera, or silently return black frames.
    """
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    # Opening a camera can "succeed" but still not deliver images, so we insist
    # on actually getting one frame before we call it working.
    ok, _ = cap.read()
    if not ok:
        cap.release()
        return None
    return cap


def fresh_box(last_box, last_seen_time, now, hold):
    """
    Return the stored box only if it is still recent enough to trust.

    Detection flickers off for a frame or two even when you haven't moved, so
    we briefly reuse the last known box. But it must expire.

    This exists as its own function because the old code applied the age check
    in only one of the two code paths: on frames where the detector was
    skipped (--detect-every > 1) it reused `last_box` unconditionally, which
    could resurrect a person position from long after `--hold` had passed.
    Routing every path through here makes that impossible.

        used  <=>  last_box is not None and (now - last_seen_time) <= hold
    """
    if last_box is None:
        return None
    if now - last_seen_time <= hold:
        return last_box
    return None


def looks_blocked(frame):
    """
    True if the frame looks like a privacy placeholder rather than a real view.

    A blocked camera on this kind of laptop does NOT fail to open. It happily
    hands you frames - they're just a flat grey card with a crossed-out camera
    icon. The detector then reports "no person" forever and it looks like a
    detection bug. A real scene has plenty of variation; the placeholder has
    almost none (measured: std 6.7 vs 40+ for a normal room).
    """
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(grey.std()) < 12.0


def load_detector(model_name="yolo11n.pt"):
    """
    Load the smallest pretrained YOLO model ("n" = nano).

    Ultralytics downloads the weights (~5 MB) the first time and caches them
    next to this file, so this only needs an internet connection once.
    """
    return YOLO(model_name)


def detect_person(model, frame, conf=0.4, imgsz=480):
    """
    Run the detector on one frame and return the *largest* person box, or None.

    Returns (x1, y1, x2, y2) as integers.

    Why the largest box: in a demo there may be a person in the background
    (a poster, someone walking past). The biggest box is almost always the
    person standing in front of the laptop, which is who we want to follow.
    """
    # verbose=False stops Ultralytics printing a line of stats for every frame.
    results = model.predict(frame, imgsz=imgsz, conf=conf, verbose=False,
                            classes=[PERSON_CLASS_ID])

    best_box = None
    best_area = 0
    for box in results[0].boxes:
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
        area = (x2 - x1) * (y2 - y1)
        if area > best_area:
            best_area = area
            best_box = (x1, y1, x2, y2)

    return best_box


def classify_position(box, frame_width, center_band=0.34):
    """
    Turn a bounding box into "LEFT", "CENTER" or "RIGHT".

    We only look at the horizontal centre of the box (cx). The frame is split
    into three zones; `center_band` is how wide the middle zone is as a
    fraction of the frame (0.34 = the middle third).

        |   LEFT   |  CENTER  |  RIGHT   |
        0        left_edge  right_edge   width
    """
    if box is None:
        return None

    x1, _, x2, _ = box
    cx = (x1 + x2) / 2

    left_edge = frame_width * (0.5 - center_band / 2)
    right_edge = frame_width * (0.5 + center_band / 2)

    if cx < left_edge:
        return "LEFT"
    if cx > right_edge:
        return "RIGHT"
    return "CENTER"


def zone_edges(frame_width, center_band=0.34):
    """Pixel x-positions of the two dividing lines. Used for drawing."""
    left_edge = int(frame_width * (0.5 - center_band / 2))
    right_edge = int(frame_width * (0.5 + center_band / 2))
    return left_edge, right_edge


def draw_overlay(frame, box, position, state, action, last_command, center_band=0.34):
    """
    Draw the zone dividers, the person box and a small status panel.

    This mutates `frame` in place, which is the normal OpenCV way of doing it.
    """
    height, width = frame.shape[:2]
    left_edge, right_edge = zone_edges(width, center_band)

    # --- the two vertical lines that split LEFT | CENTER | RIGHT ---
    cv2.line(frame, (left_edge, 0), (left_edge, height), GREY, 1)
    cv2.line(frame, (right_edge, 0), (right_edge, height), GREY, 1)

    # Zone labels along the bottom; the active zone is highlighted.
    zones = [("LEFT", left_edge // 2),
             ("CENTER", (left_edge + right_edge) // 2),
             ("RIGHT", (right_edge + width) // 2)]
    for name, x in zones:
        colour = YELLOW if name == position else GREY
        cv2.putText(frame, name, (x - 30, height - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2)

    # --- the person box ---
    if box is not None:
        x1, y1, x2, y2 = box
        cv2.rectangle(frame, (x1, y1), (x2, y2), GREEN, 2)
        # A dot on the box centre makes it obvious which zone decided the action.
        cv2.circle(frame, ((x1 + x2) // 2, (y1 + y2) // 2), 4, GREEN, -1)
        cv2.putText(frame, "person", (x1, max(y1 - 8, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 2)

    # --- status panel in the top-left corner ---
    lines = [
        f"STATE:  {state}",
        f"PERSON: {position if position else 'NONE'}",
        f"ACTION: {action}",
        f"HEARD:  {last_command if last_command else '-'}",
    ]
    # A filled dark rectangle behind the text keeps it readable on any background.
    cv2.rectangle(frame, (0, 0), (270, 20 + 22 * len(lines)), (0, 0, 0), -1)
    for i, line in enumerate(lines):
        colour = RED if line.startswith("ACTION") and action == "STOP" else WHITE
        cv2.putText(frame, line, (10, 26 + 22 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1, cv2.LINE_AA)

    return frame
