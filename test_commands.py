"""
Command matching tests - English, Hindi, Hinglish, and the false positives
that the old loose matching used to get wrong.

    python test_commands.py
"""

import behavior
import commands

FOLLOW = behavior.CMD_FOLLOW_ME
STOP = behavior.CMD_STOP

CASES = [
    # --- English, should fire ---
    ("follow me",                       FOLLOW),
    ("Follow me.",                      FOLLOW),
    ("okay follow me now",              FOLLOW),
    ("milo, follow me please",          FOLLOW),
    ("start following me",              FOLLOW),
    ("come with me",                    FOLLOW),
    ("stop",                            STOP),
    ("Stop!",                           STOP),
    ("please stop now",                 STOP),
    ("stop following me",               STOP),
    ("halt",                            STOP),

    # --- Hindi (Devanagari), should fire ---
    ("मेरे साथ चलो",                     FOLLOW),
    ("मेरे पीछे आओ",                     FOLLOW),
    ("मेरा पीछा करो",                    FOLLOW),
    ("ज़रा मेरे साथ चलो",                 FOLLOW),
    ("रुको",                            STOP),
    ("रुक जाओ",                          STOP),
    ("अभी रुक जाओ",                      STOP),
    ("बंद करो",                          STOP),

    # --- Hinglish in Roman script, should fire ---
    ("mere saath chalo",                FOLLOW),
    ("mere peeche aao",                 FOLLOW),
    ("mera peecha karo",                FOLLOW),
    ("ruko",                            STOP),
    ("ruk jao",                         STOP),
    ("milo ruk jao",                    STOP),

    # --- must NOT fire: the whole point of this module ---
    ("I follow football.",              None),
    ("We should stop for lunch later.", None),
    ("Follow this YouTube channel.",    None),
    ("I will follow up tomorrow",       None),
    ("the bus stop is around the corner", None),
    ("can you stop the music",          None),
    ("she asked me to follow her",      None),
    ("मैं फुटबॉल फॉलो करता हूँ",           None),
    ("बस स्टॉप कहाँ है",                  None),

    # --- noise and Whisper-style hallucinations ---
    ("",                                None),
    ("Thanks for watching!",            None),
    ("you",                             None),
    ("Hello there robot",               None),
    ("what is the weather",             None),
]

REPLY_CASES = [
    ("follow me",      FOLLOW, "Okay, following you."),
    ("stop",           STOP,   "Stopping."),
    ("मेरे साथ चलो",    FOLLOW, "ठीक है, मैं आपके साथ चल रहा हूँ।"),
    ("रुको",           STOP,   "ठीक है, रुक रहा हूँ।"),
]


def main():
    failures = 0

    print("--- command matching ---")
    for text, want in CASES:
        got = commands.match_command(text)
        ok = got == want
        if not ok:
            failures += 1
        print(f"[{'ok  ' if ok else 'FAIL'}] {text!r:42} -> {str(got):10} "
              f"(want {want})")

    print("\n--- spoken replies pick the right language ---")
    for text, cmd, want in REPLY_CASES:
        got = commands.reply_for(cmd, text)
        ok = got == want
        if not ok:
            failures += 1
        print(f"[{'ok  ' if ok else 'FAIL'}] {text!r:20} -> {got!r}")

    total = len(CASES) + len(REPLY_CASES)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
