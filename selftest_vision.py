"""
Headless check of the vision path: no window, just numbers.
Run it if the webcam or the detector seems broken.

    python selftest_vision.py
"""

import time

import cv2

import vision

SECONDS = 12

# Match main.py: 2 threads is fastest on this hybrid CPU, so the frame rate
# printed here reflects what you'll actually get.
try:
    import torch
    torch.set_num_threads(2)
except Exception:
    pass

cap = vision.open_camera(0)
if cap is None:
    raise SystemExit("FAIL: could not open camera 0")

ok, probe = cap.read()
if ok and vision.looks_blocked(probe):
    print("WARNING: camera is returning a blank privacy placeholder, not a real")
    print("         view. Open the privacy shutter / enable the camera, or")
    print("         nothing will ever be detected.")
else:
    print("camera OK, seeing a real scene")

t0 = time.time()
model = vision.load_detector("yolo11n.pt")
print(f"model loaded in {time.time() - t0:.1f}s")

frames = 0
hits = 0
counts = {"LEFT": 0, "CENTER": 0, "RIGHT": 0, None: 0}
times = []
start = time.time()

while time.time() - start < SECONDS:
    ok, frame = cap.read()
    if not ok:
        continue
    frame = cv2.flip(frame, 1)
    width = frame.shape[1]

    t = time.time()
    box = vision.detect_person(model, frame, conf=0.4, imgsz=480)
    times.append(time.time() - t)

    pos = vision.classify_position(box, width)
    counts[pos] += 1
    frames += 1
    if box is not None:
        hits += 1
        if frames % 10 == 0:
            print(f"  frame {frames:3d}  box={box}  pos={pos}")

cap.release()

avg = sum(times) / len(times)
print(f"\nframes: {frames}   person detected in {hits} ({100 * hits / max(frames,1):.0f}%)")
print(f"detector: {avg * 1000:.0f} ms/frame  ->  about {1 / avg:.1f} fps")
print(f"positions: {dict((k, v) for k, v in counts.items() if v)}")
