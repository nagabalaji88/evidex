"""Greetings and other small talk ("hi", "thanks", "what can you do?").

Without this, "hi" goes to document search, finds nothing and gets the refusal phrase. A
message that is only small talk gets a friendly reply instead: no search, no AI call (so no
model time or free-tier quota). A greeting in front of a real question ("Hi, what is the
warranty period?") is removed and the question is answered as usual.

Rule-based on purpose: it is predictable, instant and testable, and a real question is never
mistaken for small talk because the whole message must be made of the phrases below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each kind: phrases that may make up a whole message. Matched case-insensitively on words.
_PHRASES: dict[str, list[str]] = {
    "greeting": [r"h+i+", r"h+e+l+o+", r"hey+", r"hiya", r"yo", r"howdy", r"greetings", r"namaste", r"hola",
                 r"good (?:morning|afternoon|evening|day)"],
    "how": [r"how are (?:you|u)(?: doing)?(?: today)?", r"how(?:'s| is) it going", r"what'?s up", r"sup",
            r"how do you do"],
    "who": [r"who are (?:you|u)", r"what are (?:you|u)", r"what is evidex", r"what'?s your name",
            r"what is your name", r"introduce yourself"],
    "help": [r"help", r"what can (?:you|u) do", r"what can i ask(?: you)?", r"how (?:do|can) i use (?:this|you|evidex)",
             r"how does (?:this|it|evidex) work"],
    "thanks": [r"thanks?(?: you)?(?: so much| very much| a lot)?", r"thx", r"ty", r"many thanks", r"cheers",
               r"much appreciated", r"thanks a ton"],
    "bye": [r"bye(?: bye)?", r"goodbye", r"good night", r"see (?:you|ya)(?: later| soon)?", r"take care",
            r"that'?s all", r"talk later"],
    "ack": [r"ok(?:ay)?", r"cool", r"great", r"nice", r"got it", r"alright", r"sure", r"perfect", r"awesome",
            r"fine", r"good", r"wow", r"hmm+", r"understood", r"noted"],
}
# Words that may follow a greeting or thanks: "hi there", "thanks Evidex".
_FILLER = r"(?:\s+(?:there|evidex|team|all|everyone|bot|buddy|again|guys))?"
_UNITS = [(kind, re.compile(rf"(?:{p}){_FILLER}", re.I)) for kind, ps in _PHRASES.items() for p in ps]
# Only these may be removed from the front of a real question, and only before punctuation.
_PREFIX_KINDS = {"greeting", "thanks", "ack"}
_SEP = re.compile(r"[\s,.!?;:\-–—~()]+")


@dataclass(frozen=True)
class SmallTalk:
    kinds: tuple[str, ...]  # what the message contained, in order
    rest: str               # the real question after a leading greeting ("" if none)

    @property
    def reply_only(self) -> bool:
        return bool(self.kinds) and not self.rest


def classify(message: str) -> SmallTalk:
    text = re.sub(r"[^\w\s',.!?;:\-–—~()]", " ", message).strip()  # drop emoji and symbols
    pos, kinds = 0, []
    while True:
        m_sep = _SEP.match(text, pos)
        if m_sep:
            pos = m_sep.end()
        if pos >= len(text):
            return SmallTalk(tuple(kinds), "")
        unit = _match_unit(text, pos)
        if unit is None:
            break
        kind, end = unit
        kinds.append(kind)
        pos = end
    # Something else follows. Keep the question, minus leading greetings or thanks that
    # ended with punctuation ("Hi, what is ..." but not "help me find ...").
    rest = message.strip()
    if kinds and all(k in _PREFIX_KINDS for k in kinds) and _ends_with_punct(text, pos):
        rest = text[pos:].strip()
        return SmallTalk(tuple(kinds), rest)
    return SmallTalk((), rest)


def _match_unit(text: str, pos: int) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    for kind, rx in _UNITS:
        m = rx.match(text, pos)
        # Whole words only: "hit the target" must not match "hi".
        if m and (m.end() == len(text) or not text[m.end()].isalnum() and text[m.end()] != "'"):
            if best is None or m.end() > best[1]:
                best = (kind, m.end())
    return best


def _ends_with_punct(text: str, pos: int) -> bool:
    before = text[:pos].rstrip(" ")
    return bool(before) and before[-1] in ",.!?;:-–—~"


def reply(talk: SmallTalk, documents: int) -> str:
    kinds = set(talk.kinds)
    docs = (f"I can search your {documents} document{'s' if documents != 1 else ''}"
            if documents else "There are no documents yet. Add some on the Upload page, then I can search them")
    offer = (f"{docs}. Ask a question and I will answer from them, with the source of every answer."
             if documents else f"{docs}.")
    if kinds & {"who", "help"}:
        return ("I'm Evidex, your document assistant. I answer questions using only your documents, show where "
                "each answer comes from, and calculate from spreadsheets and CSV files. " + offer)
    if "how" in kinds:
        return "I'm doing well, thanks for asking! " + offer
    if "bye" in kinds:
        return "Goodbye! Come back any time you have a question about your documents."
    if "thanks" in kinds:
        return "You're welcome! Ask me anything else about your documents whenever you like."
    if "greeting" in kinds:
        return "Hi there! " + offer
    return "Great! Ask me another question about your documents whenever you're ready."
