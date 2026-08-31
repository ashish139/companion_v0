"""
commands.py
-----------
Turn a transcript into a command - conservatively.

The old version just asked "does the text contain the word stop?", which
means "we should stop for lunch later" halts the robot. That is not
acceptable, so this module works differently:

    1. Normalise the text to a list of words.
    2. Look for one of a fixed list of known command PHRASES.
    3. Every remaining word must be an allowed filler word
       ("please", "now", "milo", "zara", ...).

If anything unexpected is left over, it is not a command. So:

    "follow me"                  -> FOLLOW      (nothing left over)
    "milo please follow me now"  -> FOLLOW      (all filler)
    "i follow football"          -> None        ("football" is not filler)
    "follow this youtube channel"-> None        ("youtube" is not filler)
    "we should stop for lunch"   -> None        ("lunch" is not filler)

Hindi and Hinglish are handled by simply listing the phrases in both
Devanagari and Roman spelling. Sarvam's codemix mode can return either.

No LLM, no fuzzy matching - just an explicit list you can read and edit.
"""

import re
import unicodedata

import behavior
import config

# Words allowed to appear around a command without invalidating it.
# Keep this SHORT. Every word added here widens what counts as a command.
FILLER = {
    # English politeness and address
    "please", "now", "ok", "okay", "just", "hey", "robot", "yes",
    "right", "go", "ahead", "then", "come", "on", "it",
    # Hinglish / Hindi politeness
    "zara", "zara", "abhi", "bhai", "yaar", "theek", "hai", "acha", "accha",
    # Devanagari politeness
    "ज़रा", "जरा", "अभी", "ठीक", "है", "अच्छा", "कृपया", "भाई",
}

# Command phrases, longest and most specific FIRST within each list.
# STOP is checked before FOLLOW so "stop following me" means stop.
STOP_PHRASES = [
    # English
    "stop following me",
    "stop following",
    "stop",
    "halt",
    "wait",
    # Devanagari
    "रुक जाओ",
    "रुक जा",
    "रुको",
    "रुक",
    "बंद करो",
    "मत चलो",
    # Roman Hinglish
    "ruk jao",
    "ruk jaao",
    "rukjao",
    "ruko",
    "rooko",
    "ruk",
    "band karo",
]

FOLLOW_PHRASES = [
    # English
    "follow me",
    "start following me",
    "start following",
    "come with me",
    # Devanagari
    "मेरे साथ चलो",
    "मेरे साथ चल",
    "मेरे पीछे आओ",
    "मेरे पीछे आ",
    "मेरा पीछा करो",
    "पीछे आओ",
    "साथ चलो",
    # Roman Hinglish
    "mere saath chalo",
    "mere sath chalo",
    "mere saath chal",
    "mere peeche aao",
    "mere piche aao",
    "mere pichhe aao",
    "mera peecha karo",
    "mera picha karo",
    "saath chalo",
    "sath chalo",
    "peeche aao",
    "piche aao",
]


def normalise(text):
    """
    Lowercase, strip punctuation, collapse spaces. Keeps Devanagari intact.

    Careful with the punctuation stripping: an earlier version used the regex
    [^\\w\\s], but Python's \\w does NOT include Unicode combining marks, so
    it silently deleted every Hindi vowel matra - "मेरे" became "मर" and no
    Devanagari command ever matched. So we strip by Unicode category instead
    and keep letters (L), marks (M) and digits (N).
    """
    if not text:
        return []
    text = unicodedata.normalize("NFC", text).lower()
    cleaned = []
    for ch in text:
        # P = punctuation, S = symbols, C = control characters
        cleaned.append(" " if unicodedata.category(ch)[0] in ("P", "S", "C") else ch)
    return "".join(cleaned).split()


# Pre-normalise the phrase lists so both sides of the comparison go through
# exactly the same processing. Without this, a mismatch in how the text and
# the phrase are cleaned makes commands silently unmatchable.
_STOP_TOKENS = [normalise(p) for p in STOP_PHRASES]
_FOLLOW_TOKENS = [normalise(p) for p in FOLLOW_PHRASES]
_FILLER_TOKENS = set()
for _word in FILLER:
    _FILLER_TOKENS.update(normalise(_word))


def _find(tokens, want):
    """Return (start, end) of the token sequence `want` inside tokens."""
    n = len(want)
    if n == 0:
        return None
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == want:
            return i, i + n
    return None


def _matches(tokens, phrase_token_lists):
    """
    True if one of the phrases appears and everything else is filler.

    Returns the matched phrase (as a string), or None.
    """
    for want in phrase_token_lists:
        found = _find(tokens, want)
        if not found:
            continue
        start, end = found
        leftover = tokens[:start] + tokens[end:]
        if all(word in _FILLER_TOKENS for word in leftover):
            return " ".join(want)
    return None


def match_command(text):
    """
    Return behavior.CMD_STOP, behavior.CMD_FOLLOW_ME, or None.

    STOP is tested first on purpose.
    """
    tokens = normalise(text)
    if not tokens:
        return None
    if _matches(tokens, _STOP_TOKENS):
        return behavior.CMD_STOP
    if _matches(tokens, _FOLLOW_TOKENS):
        return behavior.CMD_FOLLOW_ME
    return None


def reply_for(command, heard_text):
    """
    What the robot should say back.

    If the person spoke Devanagari we answer in Hindi, otherwise English.
    Deliberately simple - it looks at the script, not the meaning.
    """
    hindi = any("ऀ" <= ch <= "ॿ" for ch in (heard_text or ""))
    if command == behavior.CMD_FOLLOW_ME:
        return ("ठीक है, मैं आपके साथ चल रहा हूँ।" if hindi
                else "Okay, following you.")
    if command == behavior.CMD_STOP:
        return "ठीक है, रुक रहा हूँ।" if hindi else "Stopping."
    return None


# Ways Whisper actually spells the robot's name.
#
# Whisper has never heard of "Golu", so it guesses, and it guesses differently
# depending on how you say it. These are the spellings we also accept, found
# by running real audio through the recogniser (see test_wake_variants.py).
#
# Each entry is matched as a whole word, or a whole pair of words - never as a
# substring - so "gold" or "golf" will not wake the robot.
# These lists come from actually running audio through Whisper, not guesswork.
# See test_wake_variants.py, which regenerates the evidence.
#
# Two spellings are deliberately EXCLUDED for "golu":
#   "hello"  - tiny.en sometimes collapses "Golu" to "Hello". Accepting it
#              would wake the robot every time anyone greets anyone.
#   "gala"   - a real English word, so it would fire on ordinary speech.
# Both are common enough that a missed wake is far better than a false one.
_KNOWN_VARIANTS = {
    # Single nonsense tokens only. Two-word spellings Whisper also produced
    # ("go lu", "go loo", "go lou") were dropped after a live run woke the
    # robot on background conversation - phrases like "go look" and "go to the
    # loo" are far too ordinary. They are barely needed now that
    # local_stt.py feeds the name to Whisper as an initial_prompt, which makes
    # it spell "Golu" correctly on every pronunciation we tested.
    "golu": [
        "golu", "gholu", "gollu", "goloo", "golou", "goolu", "gulu",
        "galu", "galoo", "galud", "galug", "golug", "galo", "gullogue",
    ],
    "milo": ["milo", "mylo", "meelo", "milow", "my low", "mee lo", "my lo"],
}


def _build_wake_variants():
    """
    Work out which spellings count as the robot's name.

    Order of preference:
      1. WAKE_VARIANTS in .env, if you have tuned it yourself
      2. the built-in list for this name
      3. just the name itself, for a name we know nothing about
    """
    name = (config.WAKE_WORD or "").strip().lower()
    if config.WAKE_VARIANTS:
        variants = list(config.WAKE_VARIANTS)
    else:
        variants = list(_KNOWN_VARIANTS.get(name, []))
    if name and name not in variants:
        variants.insert(0, name)
    return variants


WAKE_VARIANTS = _build_wake_variants()

# The robot's own name counts as filler inside a command. Without this,
# saying it twice - "Golu, Golu stop", which people do when the first one is
# ignored - leaves a stray "golu" behind and the command is rejected.
for _variant in WAKE_VARIANTS:
    _FILLER_TOKENS.update(normalise(_variant))


def split_wake_word(text):
    """
    Look for the robot's name and return (heard_it, whatever_else_was_said).

    In offline mode there is no Porcupine, so the name is spotted in the
    transcript instead. That has one nice side effect: you can say the name
    and the command in a single breath - "Milo, follow me" - and both are
    picked up from the same utterance.

        "Milo"              -> (True,  "")
        "Milo, follow me"   -> (True,  "follow me")
        "follow me"         -> (False, "follow me")
    """
    tokens = normalise(text)
    if not tokens:
        return False, ""

    for variant in WAKE_VARIANTS:
        want = variant.split()
        found = _find(tokens, want)
        if found:
            start, end = found
            rest = tokens[:start] + tokens[end:]
            return True, " ".join(rest)

    return False, " ".join(tokens)


def reply_language(text):
    """'hi-IN' if the text is Devanagari, else 'en-IN'. Used to pick a voice."""
    return "hi-IN" if any("ऀ" <= ch <= "ॿ" for ch in (text or "")) else "en-IN"
