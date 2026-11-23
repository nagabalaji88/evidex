"""Greetings and small talk get a friendly reply, not the refusal phrase."""

from __future__ import annotations

import pytest

from app.answer import smalltalk
from app.answer.schema import REFUSAL_PHRASE

from .conftest import ScriptedLLM, answer_with
from .test_answering import ALLOWANCE, ALLOWANCE_Q, make_rig  # noqa: F401 (fixture)


@pytest.mark.parametrize("msg,kind", [
    ("hi", "greeting"), ("Hi!", "greeting"), ("hiii", "greeting"), ("Hello there 👋", "greeting"),
    ("good morning", "greeting"), ("hey, how are you?", "how"), ("Thanks!", "thanks"), ("thank you so much", "thanks"),
    ("bye", "bye"), ("who are you?", "who"), ("What can you do?", "help"), ("help", "help"), ("ok", "ack"),
    ("cool, thanks", "thanks"), ("What's up", "how"),
])
def test_small_talk_alone_is_recognised(msg, kind):
    t = smalltalk.classify(msg)
    assert t.reply_only and kind in t.kinds


@pytest.mark.parametrize("msg", [
    "What is the refund policy?", "how many orders in October?", "help me find the refund policy",
    "hit rate of the campaign", "Is the warranty good for furniture?", "who approves travel claims?",
    "hi what is the warranty",  # no punctuation after the greeting: searched as typed
])
def test_real_questions_are_not_small_talk(msg):
    t = smalltalk.classify(msg)
    assert not t.reply_only and t.rest == msg


def test_leading_greeting_is_removed_from_a_question():
    t = smalltalk.classify("Hi, what is the daily allowance?")
    assert not t.reply_only and t.rest == "what is the daily allowance?"
    assert smalltalk.classify("Thanks! And in Chennai?").rest == "And in Chennai?"


def test_replies_are_friendly_and_mention_the_documents():
    hi = smalltalk.reply(smalltalk.classify("hi"), 4)
    assert hi.startswith("Hi") and "4 documents" in hi and REFUSAL_PHRASE not in hi
    assert "Upload page" in smalltalk.reply(smalltalk.classify("hello"), 0)
    assert "Evidex" in smalltalk.reply(smalltalk.classify("what can you do"), 1)
    assert smalltalk.reply(smalltalk.classify("thanks"), 1).startswith("You're welcome")


def test_hi_gets_a_reply_without_search_or_ai(make_rig):
    rig = make_rig(ScriptedLLM())  # any AI call would fail: no replies scripted
    ev, events = rig.ask("hi")
    assert ev.event == "answer" and ev.data["kind"] == "chat" and ev.data["answer"].startswith("Hi there!")
    assert ev.data["ai_called"] is False and not rig.ollama.calls("/api/chat")
    assert [e.data["stage"] for e in events if e.event == "stage"] == ["checking"]  # no search stage
    assert rig.log()["outcome"] == "smalltalk"


def test_greeting_before_a_question_still_answers(make_rig):
    rig = make_rig(ScriptedLLM(answer=answer_with(("The daily allowance is INR 2,500 in metro cities.", ALLOWANCE))))
    ev, _ = rig.ask("Hello, " + ALLOWANCE_Q)
    assert ev.data["kind"] == "text" and "2,500" in ev.data["answer"]
