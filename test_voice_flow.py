"""
Test the conversation logic in voice.py without a microphone or a speaker.

We feed it a scripted list of transcripts (as if they had been heard) and
check which ones become commands. This is what proves the rules that matter:

  * a command with no wake word, while asleep, is IGNORED
  * the name and the command in one breath both register
  * saying just the name opens a short window for a follow-up command
  * that window expires

    python test_voice_flow.py
"""

import time

import behavior
import voice

FOLLOW = behavior.CMD_FOLLOW_ME
STOP = behavior.CMD_STOP


def run_script(lines, awake_seconds=8.0):
    """
    Drive voice._run_local with scripted transcripts.

    Each line is (text, pause_before_seconds). Returns the commands that were
    dispatched, and what the robot said.
    """
    import local_stt

    said = []
    commands_fired = []
    script = list(lines)

    # --- stand in for the microphone, the recogniser and the speaker ---
    def fake_next_utterance(timeout=None):
        if not script:
            voice._stop_flag.set()
            return None
        text, pause = script.pop(0)
        if pause:
            time.sleep(pause)
        return text

    def fake_speak(text, language_code="en-IN"):
        said.append(text)

    real_next = local_stt.next_utterance
    real_speak = voice._speak_and_wait
    real_awake = voice.AWAKE_SECONDS
    local_stt.next_utterance = fake_next_utterance
    voice._speak_and_wait = fake_speak
    voice.AWAKE_SECONDS = awake_seconds

    voice._stop_flag.clear()
    voice._on_command = lambda c, t: commands_fired.append(c)
    try:
        voice._run_local()
    finally:
        local_stt.next_utterance = real_next
        voice._speak_and_wait = real_speak
        voice.AWAKE_SECONDS = real_awake
        voice._stop_flag.clear()

    return commands_fired, said


def check(label, got, want):
    ok = got == want
    print(f"[{'ok  ' if ok else 'FAIL'}] {label:56} {got}")
    if not ok:
        print(f"       expected {want}")
    return 0 if ok else 1


def main():
    failures = 0

    # 1. name + command in one breath
    fired, said = run_script([("Milo follow me", 0)])
    failures += check("'Milo follow me' -> FOLLOW", fired, [FOLLOW])
    failures += check("  ...and it replies", said, ["Okay, following you."])

    # 2. the big one: a command with no wake word must be ignored
    fired, _ = run_script([("follow me", 0), ("stop", 0)])
    failures += check("bare 'follow me' / 'stop' while asleep -> ignored",
                      fired, [])

    # 3. ordinary conversation is ignored
    fired, _ = run_script([("I follow football", 0),
                           ("we should stop for lunch", 0)])
    failures += check("unrelated conversation -> ignored", fired, [])

    # 4. name alone, then the command
    fired, said = run_script([("Milo", 0), ("stop", 0)])
    failures += check("'Milo' then 'stop' -> STOP", fired, [STOP])
    failures += check("  ...acknowledges the name first",
                      said, ["Yes?", "Stopping."])

    # 5. the awake window expires
    fired, _ = run_script([("Milo", 0), ("follow me", 0.6)], awake_seconds=0.3)
    failures += check("command after the awake window expired -> ignored",
                      fired, [])

    # 6. wake word heard but the command is not understood -> stays awake
    fired, said = run_script([("Milo what is the weather", 0),
                              ("follow me", 0)])
    failures += check("unclear command keeps it awake for a retry",
                      fired, [FOLLOW])
    failures += check("  ...and it says it did not catch that",
                      said, ["Sorry, I didn't catch that.",
                             "Okay, following you."])

    print(f"\n{'PASSED' if not failures else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
