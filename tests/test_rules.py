"""Every case here is a bug that shipped once.

The classifier reads mail literally, and the expensive mistakes have all been the
same shape: a phrase that means the opposite of what it looks like. "We are
unable to move forward with your application" contains a textbook advancement.
A layoff letter is worded exactly like a rejection. Each fixture pins one of
those down so it cannot come back.
"""
import json
import os

import pytest

from inboxjobtracker import report, rules

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "emails.json")

with open(FIXTURES, encoding="utf-8") as fh:
    EMAILS = json.load(fh)


def classify(email):
    """Status as the CSV would show it, rounds resolved."""
    row = dict(email)
    row["status"] = rules.classify(email)["status"]
    row["date"] = rules.local_date(email["received"])
    row["company"], _ = rules.guess_company(email)
    row["position"] = rules.position_from_text(email["subject"], email.get("body", ""))
    row["explicit_round"] = rules.classify(email)["explicit_round"]
    return row


@pytest.mark.parametrize("email", EMAILS, ids=[e["id"] for e in EMAILS])
def test_status(email):
    store = {e["id"]: classify(e) for e in EMAILS}
    report.assign_interview_rounds(store, gap_days=10)
    assert store[email["id"]]["status"] == email["expect"], email["why"]


def test_one_row_per_stage():
    """An application reaches each stage once; repeats are the same event
    mailed twice, and must collapse rather than double up."""
    store = {e["id"]: classify(e) for e in EMAILS}
    report.assign_interview_rounds(store, gap_days=10)
    rows = report.select_rows(store, cutoff=None)
    stages = [(rules.name_key(name), rules.position_key(pos), row["status"])
              for _, name, pos, row in rows]
    assert len(set(stages)) == len(stages)


def test_stages_never_run_backwards():
    """Acknowledge precedes an invite, which precedes a rejection."""
    store = {e["id"]: classify(e) for e in EMAILS}
    report.assign_interview_rounds(store, gap_days=10)
    rows = report.select_rows(store, cutoff=None)
    by_role = {}
    for _, name, position, row in rows:
        key = (rules.name_key(name), rules.position_key(position))
        by_role.setdefault(key, []).append(row)
    for group in by_role.values():
        group.sort(key=lambda r: r["date"])
        ranks = [report.STAGE_ORDER[r["status"]] for r in group]
        assert ranks == sorted(ranks)


def test_scheduling_a_single_interview_is_one_round():
    """A request for availability and its calendar confirmation are one
    interview, not two. Counting emails invents rounds that never happened."""
    store = {e["id"]: classify(e) for e in EMAILS if e["id"] in ("d3", "d4")}
    report.assign_interview_rounds(store, gap_days=10)
    assert {r["status"] for r in store.values()} == {"Invite to first interview"}


def test_a_later_separate_episode_is_a_second_round():
    store = {e["id"]: classify(e) for e in EMAILS if e["id"] == "d3"}
    later = dict(EMAILS[2], id="later", subject="Technical interview with the team",
                 received="2026-02-20T09:00:00Z")
    store["later"] = classify(later)
    report.assign_interview_rounds(store, gap_days=10)
    assert store["later"]["status"] == "Invite to second interview"


@pytest.mark.parametrize("text,expected", [
    ("We are unable to move forward with your application.", False),
    ("If we decide to move forward with your application, we will call.", False),
    ("Successful candidates move on to a video interview.", False),
    ("We would like to move forward with your application.", True),
])
def test_advancement_needs_to_be_real(text, expected):
    """The same phrase, negated, hypothetical, or about other people."""
    score, _ = rules.score(text, rules.NEXT_RULES, guarded=True)
    assert bool(score) is expected


@pytest.mark.parametrize("address,expected", [
    ("no-reply@us.greenhouse-mail.io", None),
    ("talent@covergenius.kula.ai", None),
    ("careers@fabrikam.example", "Fabrikam"),
])
def test_ats_vendors_are_never_the_employer(address, expected):
    assert rules.company_from_domain(address) == expected


def test_company_name_is_not_a_job_title():
    """'Thank you in your interest in Senior Software Developer' once produced
    a company called Senior Software Developer."""
    assert rules.company_from_text(
        "Thank you in your interest in Senior Software Developer", "") is None


def test_recruiter_titles_are_not_the_applied_role():
    assert rules.position_from_text(
        "Thank you for your application",
        "One of our Talent Acquisition Specialists will be in touch.") is None



# --- command line ---------------------------------------------------------

@pytest.mark.parametrize("argv,expected_days", [
    (["run"], None),
    (["7"], 7),                       # bare number: "the last 7 days"
    (["run", "--days", "30"], 30),    # option after the subcommand
    (["--days", "30", "run"], 30),    # and before it
    (["fetch", "-d", "5"], 5),
    (["demo"], None),
])
def test_days_is_accepted_on_either_side(argv, expected_days, monkeypatch):
    """Both orders read naturally, so both work, and a bare number is
    shorthand for a full run over that window."""
    from inboxjobtracker import cli

    captured = {}
    monkeypatch.setattr(
        cli, "COMMANDS",
        tuple((name, lambda cfg, args: captured.setdefault("days", args.days) and 0 or 0,
               help_text) for name, _, help_text in cli.COMMANDS))
    monkeypatch.setattr(cli.config, "load", lambda path, account=None: {})

    assert cli.main(argv) == 0
    assert captured["days"] == expected_days


def test_no_command_prints_help():
    from inboxjobtracker import cli
    assert cli.main([]) == 0
