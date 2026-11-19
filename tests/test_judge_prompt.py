"""What the judging prompt asks for about the position.

A Google mail said "I've reviewed your application for our software
engineering roles" and the model returned null - correctly, because it had
been told to quote a title exactly or say nothing. Three Google messages
named no title between them and became three applications.

The instruction is dangerous in one specific way, and the prompt has to carry
the reason with it: one title spreads to the employer's untitled mail, two
titles stop the spread altogether. An employer described two ways is worse
off than one described not at all.

Mirrors worker/test/verdict.test.ts.
"""
from inboxjobtracker.judge import PROMPT


def test_asks_for_the_field_of_work_when_no_title_is_given():
    assert "software engineering roles" in PROMPT
    assert "reduced to the discipline alone" in PROMPT


def test_demands_the_same_wording_every_time():
    assert "the same way every time" in PROMPT
    assert "two is worse than none" in PROMPT


def test_still_allows_null():
    assert "null when the mail names neither a title nor a field" in PROMPT


def test_carries_what_the_rules_found():
    """A cold read measured worse than confirming the rules' own guess."""
    assert "%(rule_position)s" in PROMPT
    assert "%(rule_company)s" in PROMPT


def test_asks_for_the_appointment_and_guards_it():
    """Amazon states the time in a sentence and labels nothing: "We're excited
    to talk to you on:" and the time underneath. Five labelled forms were
    added in two weeks and none reaches it; there is no sixth label to add,
    because the next employer writes another sentence.

    The guard matters more than the ask. That same mail carries "at least 60
    minutes", "a maximum of 10 minutes", a requisition id, a meeting id, a
    passcode and a phone number, and a model asked for "the time" can reach
    any of them. A wrong time is worse than none: the reader acts on it."""
    assert "character for character" in PROMPT
    assert "a day AND a clock time" in PROMPT
    assert "duration is not an appointment" in PROMPT
    assert '"appointment": "..."' in PROMPT
