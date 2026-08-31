"""
Regression test for the stale bounding-box bug.

The bug: with --detect-every > 1, frames where the detector was skipped
reused `last_box` without checking how old it was. So a person who had walked
out of shot could be "resurrected" long after --hold expired, and the robot
would keep turning towards where they used to be.

The rule that must hold, on EVERY frame:

    a stored box is used  <=>  (now - last_seen_time) <= hold

    python test_stale_box.py
"""

import vision

HOLD = 0.3
BOX = (100, 50, 200, 400)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok  ' if ok else 'FAIL'}] {label:52} -> {got}")
    return 0 if ok else 1


def main():
    failures = 0

    print("--- vision.fresh_box directly ---")
    failures += check("no box ever seen",
                      vision.fresh_box(None, 0.0, 10.0, HOLD), None)
    failures += check("box seen just now",
                      vision.fresh_box(BOX, 10.0, 10.0, HOLD), BOX)
    failures += check("box 0.1s old, within hold",
                      vision.fresh_box(BOX, 10.0, 10.1, HOLD), BOX)
    # The boundary is inclusive (<=). Use 0.25, which is exact in binary
    # floating point - 10.3 - 10.0 actually equals 0.3000000000000007, so
    # testing the boundary with 0.3 would fail for reasons unrelated to logic.
    failures += check("box exactly at hold boundary is still used",
                      vision.fresh_box(BOX, 10.0, 10.25, 0.25), BOX)
    failures += check("box a hair past the boundary is dropped",
                      vision.fresh_box(BOX, 10.0, 10.26, 0.25), None)
    failures += check("box 0.31s old, just past a 0.3s hold",
                      vision.fresh_box(BOX, 10.0, 10.31, HOLD), None)
    failures += check("box 5s old, long expired",
                      vision.fresh_box(BOX, 10.0, 15.0, HOLD), None)
    failures += check("hold=0 still accepts a same-instant detection",
                      vision.fresh_box(BOX, 10.0, 10.0, 0.0), BOX)

    # --- the actual bug scenario ---------------------------------------
    # Replay the main loop's logic with detect_every=3. The person is visible
    # for the first few frames then leaves. Every frame after the hold expires
    # must report no person, INCLUDING the frames where detection is skipped.
    print("\n--- simulated loop, --detect-every 3, person leaves at t=0.20 ---")
    detect_every = 3
    fps = 15.0
    person_leaves_at = 0.20

    last_box, last_seen = None, 0.0
    bad_frames = []
    for i in range(1, 46):
        now = i / fps
        if i % detect_every == 0:
            detected = BOX if now < person_leaves_at else None
            if detected is not None:
                last_box, last_seen = detected, now

        box = vision.fresh_box(last_box, last_seen, now, HOLD)

        # After the person left AND the hold expired, box must be None.
        expired = now > (last_seen + HOLD)
        if expired and box is not None:
            bad_frames.append((i, round(now, 2), round(now - last_seen, 2)))

    if bad_frames:
        failures += 1
        print(f"[FAIL] stale box survived on {len(bad_frames)} frame(s): "
              f"{bad_frames[:5]}")
    else:
        print("[ok  ] no stale box on any frame, skipped or otherwise")

    # Sanity: the box IS used during the hold window, or the fix is useless.
    used_during_hold = vision.fresh_box(BOX, 1.00, 1.15, HOLD) == BOX
    failures += check("box still bridges flicker inside the hold window",
                      used_during_hold, True)

    print(f"\n{'PASSED' if not failures else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
