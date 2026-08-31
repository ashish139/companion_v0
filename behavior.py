"""
behavior.py
-----------
The "brain". It takes

    current state  +  (maybe) a voice command  +  (maybe) a person position

and returns

    new state  +  movement action  +  (maybe) something to say out loud

This file is deliberately written as plain functions with no side effects:
it never touches the camera, the microphone or the speaker. That makes the
rules easy to read and easy to test on their own (see the __main__ block).
"""

# --- states ---
IDLE = "IDLE"            # started up, hasn't been told to do anything yet
FOLLOWING = "FOLLOWING"  # actively tracking the person
STOPPED = "STOPPED"      # explicitly told to stop

# --- movement actions we would send to a real robot ---
TURN_LEFT = "TURN_LEFT"
FORWARD = "FORWARD"
TURN_RIGHT = "TURN_RIGHT"
STOP = "STOP"

# --- voice commands the speech module can produce ---
CMD_FOLLOW_ME = "follow me"
CMD_STOP = "stop"

# What to say when a command is accepted.
SPOKEN_REPLY = {
    CMD_FOLLOW_ME: "Okay, following you.",
    CMD_STOP: "Stopping.",
}

# Which movement to make for each person position while FOLLOWING.
# The person being on the left means the robot must turn left to face them.
POSITION_TO_ACTION = {
    "LEFT": TURN_LEFT,
    "CENTER": FORWARD,
    "RIGHT": TURN_RIGHT,
}


def decide(state, command, position):
    """
    Run one step of the state machine.

    Arguments
        state    : IDLE / FOLLOWING / STOPPED
        command  : CMD_FOLLOW_ME, CMD_STOP, or None if nothing was heard
        position : "LEFT" / "CENTER" / "RIGHT", or None if no person is visible

    Returns
        (new_state, action, say_text)

        say_text is None on most calls. It is only set on the frame where a
        voice command was actually accepted, which is what stops the robot
        repeating "Okay, following you." on every single video frame.
    """
    new_state = state
    say_text = None

    # --- step 1: a voice command (if any) decides the state ---
    if command == CMD_FOLLOW_ME:
        new_state = FOLLOWING
        say_text = SPOKEN_REPLY[CMD_FOLLOW_ME]
    elif command == CMD_STOP:
        new_state = STOPPED
        say_text = SPOKEN_REPLY[CMD_STOP]

    # --- step 2: the state (plus what the camera sees) decides the action ---
    if new_state == FOLLOWING:
        # No person in frame -> we have nothing to follow, so hold still.
        action = POSITION_TO_ACTION.get(position, STOP)
    else:
        # IDLE and STOPPED never move.
        action = STOP

    return new_state, action, say_text


# A tiny self-test so you can check the rules without a camera or microphone:
#     python behavior.py
if __name__ == "__main__":
    checks = [
        # (state,     command,        position,   expected new_state, expected action)
        (IDLE,        None,           "LEFT",     IDLE,      STOP),
        (IDLE,        CMD_FOLLOW_ME,  "LEFT",     FOLLOWING, TURN_LEFT),
        (FOLLOWING,   None,           "CENTER",   FOLLOWING, FORWARD),
        (FOLLOWING,   None,           "RIGHT",    FOLLOWING, TURN_RIGHT),
        (FOLLOWING,   None,           None,       FOLLOWING, STOP),
        (FOLLOWING,   CMD_STOP,       "CENTER",   STOPPED,   STOP),
        (STOPPED,     None,           "LEFT",     STOPPED,   STOP),
        (STOPPED,     CMD_FOLLOW_ME,  "RIGHT",    FOLLOWING, TURN_RIGHT),
    ]
    for state, command, position, want_state, want_action in checks:
        got_state, got_action, say = decide(state, command, position)
        ok = (got_state == want_state) and (got_action == want_action)
        print(f"[{'ok ' if ok else 'FAIL'}] {state:9} + {str(command):11} + "
              f"{str(position):7} -> {got_state:9} {got_action:11} say={say}")
