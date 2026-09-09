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


# --- one application, several spellings of its title ----------------------

@pytest.mark.parametrize("variant", [
    "Senior Backend Engineer",
    "Senior Backend Engineer, AMER",
    "Senior Backend Engineer, AMER - Evergreen",
    "Senior Backend Engineer (Remote, Canada)",
    "Sr. Backend Engineer - Full-Time",
    "Senior Backend Engineer, Req 44120",
])
def test_ats_decoration_is_not_a_different_role(variant):
    """GitLab's acknowledgement said "Senior Backend Engineer, AMER -
    Evergreen", the screening mail dropped "Evergreen" and the rejection dropped
    the region. Three spellings filed one application under three roles, which
    split its history and put a second Acknowledge in the spreadsheet."""
    assert rules.position_key(variant) == rules.position_key("Senior Backend Engineer")


@pytest.mark.parametrize("position", [
    "Senior Security Engineer, AI Security",
    "Software Development Engineer II, AWS IAM Identity Center",
    "Software Engineer, Machine Learning",
])
def test_a_specialism_is_not_decoration(position):
    """The tail of a title usually names the team or the discipline, and that is
    a different job. Only region, pipeline and employment-type words go."""
    assert rules.position_key(position) != rules.position_key(position.split(",")[0])


def test_a_bare_hyphen_is_part_of_the_word():
    """"Full-Stack" is one word; only a spaced hyphen separates segments."""
    assert rules.strip_position_noise(
        "Full-Stack Software Development Engineer") == "Full-Stack Software Development Engineer"
    assert rules.strip_position_noise(
        "Full-Stack Engineer - Remote") == "Full-Stack Engineer"


def test_title_variants_collapse_to_one_history():
    """The four GitLab mails are one application: a receipt, a screening
    request, the interview, the rejection — three rows, read in date order."""
    mails = [
        ("2026-08-29", "Acknowledge", "Senior Backend Engineer, AMER"),
        ("2026-08-30", "Acknowledge", "Senior Backend Engineer, AMER - Evergreen"),
        ("2026-08-30", "Invite to first interview", "Senior Backend Engineer"),
        ("2026-09-09", "Reject", "Senior Backend Engineer"),
    ]
    store = {}
    for i, (date, status, position) in enumerate(mails):
        store["g%d" % i] = {
            "company": "GitLab", "position": position, "status": status,
            "date": date, "received": date + "T09:00:00Z",
            "from_address": "kwilson-ext@gitlab.com", "subject": "GitLab",
        }
    rows = report.select_rows(store, cutoff=None)
    assert [(r["date"], r["status"]) for _, _, _, r in rows] == [
        ("2026-08-29", "Acknowledge"),
        ("2026-08-30", "Invite to first interview"),
        ("2026-09-09", "Reject"),
    ]
    # and all three read under one title, the plainest one seen
    assert {position for _, _, position, _ in rows} == {"Senior Backend Engineer"}


# --- a round that never happened -------------------------------------------

def test_onsite_alone_is_a_work_arrangement():
    """A Procom contract posting said "hybrid with 3 days onsite at our client's
    Richmond office" and the round cue read it as a final round."""
    assert rules.explicit_round("This position is hybrid with 3 days onsite.") is None
    assert rules.explicit_round("We would like to invite you to an onsite interview.") == 3
    assert rules.explicit_round("The final round is with the VP.") == 3


def test_a_precondition_is_not_an_advancement():
    """"before I can move forward" is the recruiter asking for paperwork. The
    advancement is what is being withheld, not what is being offered."""
    text = ("Hi Michael, it was nice speaking with you today! I will need a few "
            "items from you before I can move forward with your application.")
    assert rules.score(text, rules.NEXT_RULES, guarded=True)[0] == 0
    # the real thing still scores
    assert rules.score("We would like to move forward with your application.",
                       rules.NEXT_RULES, guarded=True)[0] > 0


def test_a_stated_round_cannot_outrun_the_history():
    """Whatever wording claims, a first interview is a first interview: there is
    no third without a second. The cap is what makes the class impossible."""
    store = {"a": {"company": "Acme", "position": "AI Engineer",
                   "status": rules.INTERVIEW, "date": "2026-09-08",
                   "received": "2026-09-08T09:00:00Z", "explicit_round": 3,
                   "subject": "Bid Form", "from_address": "r@agency.com"}}
    report.assign_interview_rounds(store, gap_days=10)
    assert store["a"]["status"] == "Invite to first interview"


def test_a_stated_final_round_still_counts_when_earned():
    mails = [("2026-07-01", None, "Phone screen"),
             ("2026-08-01", None, "Technical interview with the team"),
             ("2026-09-01", 3, "Final round with the VP")]
    store = {}
    for i, (date, stated, subject) in enumerate(mails):
        store["r%d" % i] = {"company": "Acme", "position": "AI Engineer",
                            "status": rules.INTERVIEW, "date": date,
                            "received": date + "T09:00:00Z",
                            "explicit_round": stated, "subject": subject,
                            "from_address": "r@acme.com"}
    report.assign_interview_rounds(store, gap_days=10)
    assert store["r2"]["status"] == "Invite to third interview"


def test_a_farewell_is_not_an_invitation():
    """"Best wishes for your next steps" closes a rejection, and closed the
    government satisfaction survey that got published as a first interview."""
    survey = ("Welcome to WorkBC! Please tell us about your experience by "
              "answering this survey. Best wishes for your next steps, "
              "Your WorkBC Employment Services Team")
    assert rules.score(survey, rules.NEXT_RULES, guarded=True)[0] == 0
    assert rules.score("Here are the next steps for your interview.",
                       rules.NEXT_RULES, guarded=True)[0] > 0


def test_a_rejection_may_still_wish_you_well():
    """The guard is for advancement wording only; REJECT scoring is unguarded,
    so the farewell that so often ends a rejection still reads as one."""
    text = ("Unfortunately we are unable to move forward with your application. "
            "We wish you the best in your job search.")
    assert rules.classify({"subject": "Your application", "body": text,
                           "from_address": "careers@acme.com",
                           "from_name": "Acme"})["status"] == rules.REJECT


# --- the audit tier gets a share of every batch ----------------------------

def test_the_audit_is_not_starved_by_uncertain_items():
    """There are always more uncertain items than a batch holds. Concatenating
    and truncating meant the confident verdicts sat at the back for ever, so
    the half where the costly mistakes live was never once reviewed."""
    from inboxjobtracker import cli
    review = [{"reason": "uncertain", "rule_status": "Unclear"} for _ in range(100)]
    audit = [{"reason": "audit", "rule_status": "Acknowledge"} for _ in range(100)]
    queue = cli.build_queue(review, audit, batch_size=40, audit_share=0.25)
    assert len(queue) == 40
    assert sum(1 for i in queue if i["reason"] == "audit") == 10


def test_each_tier_takes_what_the_other_leaves():
    from inboxjobtracker import cli
    review = [{"reason": "uncertain", "rule_status": "Unclear"} for _ in range(3)]
    audit = [{"reason": "audit", "rule_status": "Acknowledge"} for _ in range(100)]
    assert len(cli.build_queue(review, audit, 40, 0.25)) == 40
    assert len(cli.build_queue(review, [], 40, 0.25)) == 3


def test_the_audit_share_is_spent_where_being_wrong_costs_most():
    """A receipt misread as a receipt costs nothing; an invented interview or a
    missed rejection changes what the reader believes about a real application."""
    from inboxjobtracker import cli
    audit = ([{"reason": "audit", "rule_status": "Acknowledge"}] * 20
             + [{"reason": "audit", "rule_status": "Invite to interview"}]
             + [{"reason": "audit", "rule_status": "Reject"}])
    queue = cli.build_queue([], audit, batch_size=2, audit_share=0.25)
    assert [i["rule_status"] for i in queue] == ["Invite to interview", "Reject"]


def test_a_hypothetical_rejection_rejects_nobody():
    """Reject scoring was the one tier with no guard at all, on the reasoning
    that a rejection is made of negations. But a *conditional* rejection is
    still not one, and every ATS receipt carries this dashboard legend."""
    legend = ("If you see the job moved to an inactive state, that means the "
              "position is either no longer open, you withdrew from "
              "consideration, or you were not selected for the role.")
    assert rules.score(legend, rules.REJECT_RULES, guarded=True,
                       cues=rules.REJECT_CUES)[0] == 0


@pytest.mark.parametrize("text", [
    "Unfortunately, you were not selected for the role. We wish you the best.",
    "After reviewing your credentials, we have decided to move forward with other applicants.",
    "We are unable to move forward with your application at this time.",
])
def test_the_guard_never_touches_a_real_rejection(text):
    """A rejection is made of negations and ends in a farewell, so those two
    cues must never apply to it - only the hypothetical ones do."""
    assert rules.score(text, rules.REJECT_RULES, guarded=True,
                       cues=rules.REJECT_CUES)[0] > 0
