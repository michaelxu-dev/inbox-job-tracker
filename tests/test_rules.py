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
    # Per attempt at the role: applying to one role twice reaches Acknowledge
    # twice, which is two applications and not a repeat.
    stages = [(rules.name_key(name), rules.position_key(pos), a, row["status"])
              for _, name, pos, row, a in rows]
    assert len(set(stages)) == len(stages)


def test_stages_never_run_backwards():
    """Acknowledge precedes an invite, which precedes a rejection."""
    store = {e["id"]: classify(e) for e in EMAILS}
    report.assign_interview_rounds(store, gap_days=10)
    rows = report.select_rows(store, cutoff=None)
    by_role = {}
    for _, name, position, row, a in rows:
        key = (rules.name_key(name), rules.position_key(position), a)
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
    ("no-reply@ats.rippling.com", None),
    ("help.candidate@njoyn.com", None),
    ("talentcentral@shl.com", None),
    ("careers@fabrikam.example", "Fabrikam"),
])
def test_ats_vendors_are_never_the_employer(address, expected):
    assert rules.company_from_domain(address) == expected


def test_ats_mail_is_filed_under_the_employer_it_names():
    """A Rippling-hosted receipt from 'D-Wave Quantum' was filed under Rippling,
    because the sender domain was tried before the display name and subject."""
    email = next(e for e in EMAILS if e["id"] == "d-rippling")
    company, _ = rules.guess_company(email)
    assert rules.name_key(company).startswith("dwavequantum")


def test_domain_name_contradicted_by_the_mail_is_flagged(monkeypatch):
    """The next unlisted ATS host will repeat the Rippling mistake. The status
    was confidently right, so the audit never reached it; the disagreement
    between domain and display name is what has to send it to review."""
    monkeypatch.setattr(rules, "ATS_DOMAINS", rules.ATS_DOMAINS - {"ats.rippling.com"})
    email = next(e for e in EMAILS if e["id"] == "d-rippling")
    company, source = rules.guess_company(email)
    assert (company, source) == ("Rippling", "domain")
    assert rules.company_conflict(email, company, source) == "D-Wave Quantum"


@pytest.mark.parametrize("from_name,from_address,subject", [
    ("Thales Group", "recruiting@jobalerts.thalesgroup.com", "Thank you for applying"),
    ("Casey Bewley", "casey@motorolasolutions.com", "Your interview at Motorola Solutions."),
    ("", "no-reply@coalitioninc.com", "Thank you for applying to Coalition!"),
])
def test_spelling_of_the_same_employer_is_not_a_conflict(from_name, from_address, subject):
    email = {"from_name": from_name, "from_address": from_address,
             "subject": subject, "body": ""}
    company, source = rules.guess_company(email)
    assert rules.company_conflict(email, company, source) is None


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
    assert [(r["date"], r["status"]) for _, _, _, r, _a in rows] == [
        ("2026-08-29", "Acknowledge"),
        ("2026-08-30", "Invite to first interview"),
        ("2026-09-09", "Reject"),
    ]
    # and all three read under one title, the plainest one seen
    assert {position for _, _, position, _, _a in rows} == {"Senior Backend Engineer"}


def test_untitled_booking_joins_the_role_named_under_another_spelling():
    """A booking mail names no role, and the receipt that does was guessed
    under another name (Treasure AI's came out as "Teamtailor Mail" from its
    domain). Both are filed under one employer, so the title must reach the
    booking too, or one application reads as two roles."""
    store = {
        "ack": {"company": "Fabrikam", "company_source": "domain",
                "position": "Staff Fullstack Engineer", "status": "Acknowledge",
                "date": "2026-08-29", "received": "2026-08-29T09:00:00Z",
                "from_address": "no-reply@fabrikam.ai"},
        "call": {"company": "Fabrikam AI", "company_source": "agent",
                 "position": None, "status": "Invite to first interview",
                 "date": "2026-09-01", "received": "2026-09-01T09:00:00Z",
                 "from_address": "jane.doe@fabrikam.ai"},
    }
    rows = report.select_rows(store, cutoff=None)
    assert {(name, position) for _, name, position, _, _a in rows} == {
        ("Fabrikam AI", "Staff Fullstack Engineer")}


# --- a round that never happened -------------------------------------------

def test_onsite_alone_is_a_work_arrangement():
    """A Procom contract posting said "hybrid with 3 days onsite at our client's
    Richmond office" and the round cue read it as a final round."""
    assert rules.explicit_round("This position is hybrid with 3 days onsite.") is None
    assert rules.explicit_round("We would like to invite you to an onsite interview.") == 3
    assert rules.explicit_round("The final round is with the VP.") == 3


def test_a_preview_of_a_later_round_is_not_this_round():
    """An Initech interview email confirming the first interview also previewed
    what came after it - "Our 2nd interview is more technical by nature and
    will include live coding" - and the round cue read that preview as the
    invitation itself, promoting a first interview to a second."""
    text = ("This interview will be conversational, covering behavioral and "
            "technical questions. Our 2nd interview is more technical by "
            "nature and will include live coding.")
    assert rules.explicit_round(text) is None
    # a genuine second-round invitation must still count
    assert rules.explicit_round("We'd like to invite you to your second interview.") == 2


def test_a_precondition_is_not_an_advancement():
    """"before I can move forward" is the recruiter asking for paperwork. The
    advancement is what is being withheld, not what is being offered."""
    text = ("Hi Michael, it was nice speaking with you today! I will need a few "
            "items from you before I can move forward with your application.")
    assert rules.score(text, rules.NEXT_RULES, guarded=True)[0] == 0
    # the real thing still scores
    assert rules.score("We would like to move forward with your application.",
                       rules.NEXT_RULES, guarded=True)[0] > 0


def test_employer_domain_and_ats_tenant_are_one_employer():
    """Thales acknowledged from thalesgroup.com ("Thalesgroup") and rejected from
    its Workday tenant ("Thales"): one application showed under two employers."""
    def row(status, date, company, source, address):
        return {"company": company, "company_source": source,
                "position": "Sr Software Developer", "status": status,
                "date": date, "received": date + "T09:00:00Z",
                "explicit_round": None, "subject": "Thales Group",
                "from_address": address}
    store = {
        "ack": row(rules.ACK, "2026-08-27", "Thalesgroup", "domain",
                   "recruiting@jobalerts.thalesgroup.com"),
        "rej": row(rules.REJECT, "2026-08-28", "Thales", "agent", "thales@myworkday.com"),
    }
    rows = report.select_rows(store, cutoff=None)
    assert {name for _, name, _, _, _a in rows} == {"Thales"}


@pytest.mark.parametrize("a,b,same", [
    ("Thalesgroup", "Thales", True),
    ("D-Wave Quantum Inc.", "D-Wave Quantum", True),
    ("Coalitioninc", "Coalition", True),
    ("Zinc", "Z", False),
])
def test_corporate_suffix_is_not_a_different_employer(a, b, same):
    assert (rules.name_key(a) == rules.name_key(b)) is same


@pytest.mark.parametrize("a,b,same", [
    # A company whose name is its domain: the acknowledgement came through an
    # ATS as "Super", the interview invitation from the company itself as
    # "Super.com", and they were two applications that never joined.
    ("Super.com", "Super", True),
    ("Monzo.io", "Monzo", True),
    # Only a literal dotted ending. Stripping "com" after the punctuation has
    # gone would file these under "Inter" and "Tele".
    ("Intercom", "Inter", False),
    ("Telecom Italia", "Tele Italia", False),
])
def test_a_domain_ending_is_not_a_different_employer(a, b, same):
    assert (rules.name_key(a) == rules.name_key(b)) is same


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


def test_a_stage_the_process_may_contain_is_not_an_offer():
    """"For Engineering roles, this may also include a Technical Interview" is
    the company describing its funnel. Nothing has been offered to the reader."""
    text = ("For Engineering roles, this may also include a Technical Interview. "
            "Depending on the position, you might be asked to complete a skills test.")
    assert rules.score(text, rules.NEXT_RULES, guarded=True)[0] == 0


def test_permission_is_still_an_invitation():
    """The cue is about what a process contains, not what you are allowed to do:
    "you may schedule a call" offers something and must keep scoring."""
    assert rules.score("You may schedule a call at your convenience.",
                       rules.NEXT_RULES, guarded=True)[0] > 0
    assert rules.score("We would like to invite you to a technical interview.",
                       rules.NEXT_RULES, guarded=True)[0] > 0


def test_a_benefits_programme_is_not_an_employer():
    """Unemployment benefits and the state job boards beside them talk about
    jobs, applications, resumes and next steps - everything the job-context
    check looks for - while deciding nothing about the reader."""
    mail = {"subject": "Applied for EI? Now set up your Job Bank account",
            "from_address": "no-reply-jobbank-ei@example.gov",
            "from_name": "Job Bank",
            "body": "Your next step after applying for Employment Insurance (EI) "
                    "is to set up your Job Bank account. Submit applications in "
                    "just a few clicks and get matched with jobs."}
    assert rules.classify(mail)["status"] == rules.UNCLEAR


def test_the_gate_yields_to_a_real_decision():
    """An employer that happens to mention a programme still gets read: the gate
    only settles mail whose signal was weak to begin with."""
    mail = {"subject": "Interview invitation",
            "from_address": "careers@acme.com", "from_name": "Acme",
            "body": "We would like to invite you to an interview next week. "
                    "Please share your availability. If you are receiving "
                    "Employment Insurance this will not affect your application."}
    assert rules.classify(mail)["status"].startswith("Invite")


# --- one employer, one name ----------------------------------------------
#
# Every case here put a single application under two employers, or under an
# employer that does not exist. A split history is worse than a missing one:
# the reader sees an acknowledgement with no outcome and a rejection with no
# application, and neither row admits the other exists.

@pytest.mark.parametrize("address", [
    "mckayla.frankland@contoso.na.teamtailor-mail.com",
    "no-reply@us.greenhouse-mail.io",
])
def test_vendor_mail_domains_are_not_the_employer(address):
    """Teamtailor gives each customer its own sending domain, so the employer
    looks like a subdomain of the vendor. Read literally it yields an employer
    called "Teamtailor Mail", and every Teamtailor customer files under it."""
    assert rules.company_from_domain(address) is None


@pytest.mark.parametrize("display,expected", [
    ("Dana Reed - Contoso", "Contoso"),
    # Both halves name the employer, which used to produce "Contoso Contoso".
    ("Contoso Recruitment Team - Contoso", "Contoso"),
    # The right half is a role, not an employer, so the left half stands.
    ("Contoso - Senior Backend Engineer", "Contoso"),
    # ...and so does it when the right half is nothing but vendor noise.
    ("Contoso - Careers", "Contoso"),
])
def test_the_employer_is_the_half_after_the_dash(display, expected):
    assert rules.company_from_name(display) == expected


@pytest.mark.parametrize("display,expected", [
    # "Fabrikam Group" has exactly the shape of a person's name, and the guard
    # against recruiters' names dropped it - leaving the employer unknown on
    # the only mail that named it.
    ("Fabrikam Group", "Fabrikam Group"),
    ("Northwind Labs", "Northwind Labs"),
    # A real personal name is still a recruiter, not the employer.
    ("Jane Smith", None),
])
def test_a_company_can_be_two_words(display, expected):
    assert rules.company_from_name(display) == expected


def test_an_article_is_not_part_of_the_name():
    """"The Contoso Talent Team" is Contoso. Left as "The Contoso" it reads as
    a personal name and is dropped entirely."""
    assert rules.company_from_name("The Contoso Talent Team") == "Contoso"


def test_a_possessive_is_not_part_of_the_name():
    """"Remarcable, Inc.'s Hiring Team" left the possessive attached."""
    assert rules.name_key(rules.company_from_name("Contoso, Inc.'s Hiring Team")) \
        == rules.name_key("Contoso")


@pytest.mark.parametrize("variant", [
    "Contoso Inc", "Contoso Inc.", "Contoso, Inc.", "Contoso Ltd",
    "Contoso Limited", "Contoso LLC", "Contoso Corp.", "The Contoso",
])
def test_a_legal_suffix_is_not_a_different_employer(variant):
    """The receipt said "Tucows Inc." and the rejection just "Tucows", so one
    application appeared twice with one stage each."""
    assert rules.name_key(variant) == rules.name_key("Contoso")


def test_a_job_word_ends_where_the_word_ends():
    """"For Engineering roles, this may also include a Technical Interview"
    produced the job title "For Engineer", and filed an acknowledgement under a
    role nobody applied for."""
    assert rules.position_from_text(
        "Thank you for applying to Contoso",
        "For Engineering roles, this may also include a Technical Interview.") is None


def test_restored_casing_beats_a_titlecased_domain():
    """"ea.com" can only yield "Ea"; a mail that writes "EA" gives the real
    spelling. Both come from the domain, so only the tie-break separates them,
    and without it the winner was whichever mail was read first."""
    rows = {
        "a": {"company": "Ea", "company_source": "domain",
              "from_address": "eacareers@ea.example"},
        "b": {"company": "EA", "company_source": "domain",
              "from_address": "eacareers@ea.example"},
    }
    assert report.canonical_names(rows)[rules.name_key("EA")] == "EA"


def test_one_application_keeps_one_spelling():
    """The receipt came through the ATS and the rejection from the employer's
    own domain, so one map answered for each and they disagreed. The history
    then read "Contoso" on one line and "Contoso Inc" on the next."""
    store = {
        "a": {"company": "Contoso Inc", "company_source": "text", "status": "Acknowledge",
              "position": "Senior Backend Engineer", "date": "2026-08-01",
              "received": "2026-08-01T09:00:00Z", "subject": "Thanks for applying",
              "from_address": "no-reply@us.greenhouse-mail.io"},
        "b": {"company": "Contoso", "company_source": "domain", "status": "Reject",
              "position": "Senior Backend Engineer", "date": "2026-09-01",
              "received": "2026-09-01T09:00:00Z", "subject": "An update",
              "from_address": "no-reply@contoso.example"},
    }
    names = {name for _, name, _, _, _a in report.select_rows(store, cutoff=None)}
    assert len(names) == 1


# --- both signals at once -------------------------------------------------

def _verdict(body):
    return rules.classify({
        "subject": "An update on your application",
        "from_name": "Contoso Careers",
        "from_address": "no-reply@contoso.example",
        "body": body,
    })


def test_a_lopsided_mix_is_not_a_close_call():
    """A rejection that also offers feedback trips both rule sets, and every
    one of them was queued for review however lopsided the scores. GitLab's
    scored 23 against 10 and sat in the queue asking a question nobody needed
    to answer."""
    verdict = _verdict(
        "We regret to inform you that we will not be proceeding with your "
        "application for the Senior Backend Engineer role. Unfortunately we have "
        "decided to move forward with other candidates. If you would like, please "
        "let us know your availability and we can share feedback about next steps.")
    # 43 rather than 35 since "move forward with other candidates" became a
    # rule of its own: the same sentence, counted once more. The point of this
    # test is the gap between the two scores, not either number.
    assert (verdict["reject_score"], verdict["next_score"]) == (43, 14)
    assert verdict["status"] == "Reject"
    assert verdict["confidence"] == "high"


def test_a_close_mix_is_still_a_question():
    """Nearer than double and it stays a real question: a rejection inviting the
    reader to apply again reads exactly like an advancement, and no score
    separates those two readings."""
    verdict = _verdict(
        "We regret to inform you that another candidate was selected for this "
        "position. Please let us know your availability if you would like "
        "feedback on your application.")
    assert (verdict["reject_score"], verdict["next_score"]) == (10, 8)
    assert verdict["confidence"] == "low"


def test_a_job_board_advert_is_not_an_interview_however_it_scores():
    """Found on a real mailbox in September 2026, three at once, all filed as
    "Invite to first interview".

    The gate used to yield to a confident score - `rej < 8 and nxt < 8` - so
    that a real decision relayed by a board would still win. What actually came
    through was a LinkedIn notification digest in which a *stranger's* post read
    `Rest in peace, "Book a call" or "Demo"`, matching the "book a call" cue for
    10 points. The scorer reads a digest as though every sentence in it were the
    sender speaking to the reader; on these domains it never is.
    """
    digest = rules.classify({
        "subject": "Saritha Ramesh at Amazon commented on Trevor Ramos' post: "
                   "Super happy for you :) Congratulations!!",
        "from_name": "LinkedIn",
        "from_address": "notifications-noreply@linkedin.com",
        "body": 'Rumman Sadiq posted: Don\'t call us mad for flipping the '
                'entire process. Rest in peace, "Book a call" or "Demo"',
    })
    assert digest["status"] == rules.UNCLEAR
    assert digest["confidence"] == "high"
    # The score is still computed and still high. That it no longer decides
    # anything here is the point.
    assert digest["next_score"] >= 8

    alert = rules.classify({
        "subject": "Web Developer at ShearComfort Ltd. and 2 more new jobs",
        "from_name": "Indeed Job Alerts",
        "from_address": "donotreply@jobalert.indeed.com",
        "body": "3 new react opportunities in Vancouver, BC. Jobs 1-3 of 3 new "
                "jobs. Easily apply. Schedule an interview with employers.",
    })
    assert alert["status"] == rules.UNCLEAR
    assert alert["confidence"] == "high"


def test_a_subdomain_of_a_job_board_is_still_a_job_board():
    """Indeed sends its alerts from jobalert.indeed.com, not indeed.com. An
    exact-match gate would have missed every one of them."""
    assert rules.is_job_board("donotreply@jobalert.indeed.com")
    assert rules.is_job_board("updates-noreply@linkedin.com")
    assert not rules.is_job_board("careers@indeed-partner.example")


def test_a_bulletin_about_other_people_is_not_an_answer_to_you():
    """The scorer's founding assumption - that a sentence in the body is the
    sender speaking to the reader about their application - is false for a
    digest, which is a dozen strangers' posts stapled together.

    The score is deliberately still high here. A gate that yielded to it would
    be decorative, because the score is read out of the very text the gate says
    is not addressed to the reader.
    """
    verdict = rules.classify({
        "subject": "Jane commented on your post",
        "from_name": "Some Network",
        "from_address": "no-reply@social.example",
        "body": "Jane commented on Bob's post: Congratulations! Book a call with us.",
    })
    assert verdict["status"] == rules.UNCLEAR
    assert verdict["confidence"] == "high"
    assert verdict["next_score"] >= 8


def test_a_list_of_openings_is_an_advert_from_any_domain():
    """Job boards are gated by address, but the same adverts arrive relayed,
    white-labelled and from domains no list will ever hold."""
    verdict = rules.classify({
        "subject": "10 new jobs matching your search",
        "from_name": "Careers Digest",
        "from_address": "alerts@somewhere.example",
        "body": "10 new jobs match your search in Vancouver. Easily apply.",
    })
    assert verdict["status"] == rules.UNCLEAR


def test_an_employers_own_reply_is_not_a_bulletin():
    """The guard on the guard. `unsubscribe` is deliberately not a marker: ATS
    mail carries it constantly, and gating on it would throw away real
    rejections and real invitations."""
    for body in [
        "Thank you for applying. Easily track your application status here. "
        "Unsubscribe from these emails.",
        "We would like to schedule a call with you next week.",
        "We regret to inform you that we will not be moving forward.",
    ]:
        verdict = rules.classify({
            "subject": "Your application",
            "from_name": "Contoso",
            "from_address": "no-reply@contoso.example",
            "body": body,
        })
        assert "bulletin" not in verdict["note"], body


def test_a_newsletter_telling_you_to_act_is_not_an_employer_acting():
    """The same settlement agency, published as a first interview twice.

    First time the phrase was "take the next step in your career journey", and
    the lookahead on the NEXT rule was narrowed to exclude it. This time it was
    a bare heading, "## Take your next step!", which that lookahead cannot see:
    the giveaway is in front of the match, not behind it.
    """
    verdict = rules.classify({
        "subject": "Lost your job? Here's how one newcomer rebuilt her career in Canada",
        "from_name": "ISSofBC",
        "from_address": "communications@issbc.org",
        "body": "Immigrant Services Society of BC welcomes and supports newcomers "
                "to settle in their communities, learn English, study, and find "
                "work in British Columbia.\n\n"
                '"One resume at a time. One interview at a time. Treat every '
                'interview as practice." - Sahil Chopra, B-Hired client\n\n'
                "## Take your next step!\n"
                "* Register for upcoming free career events and job fairs.",
    })
    assert verdict["status"] == rules.UNCLEAR
    assert verdict["next_score"] == 0


def test_an_employer_taking_the_next_step_still_counts():
    """The cue needs the possessive. "We would like to take the next step with
    you" is a real advancement and the exclusion must not reach it."""
    verdict = rules.classify({
        "subject": "Your application",
        "from_name": "Contoso",
        "from_address": "no-reply@contoso.example",
        "body": "We would like to take the next step with you.",
    })
    assert verdict["next_score"] > 0


def test_moving_forward_with_other_people_is_a_decline():
    """Caught on a real mailbox, shown briefly and then gone: a plain rejection
    the rules scored at nothing.

    It never says no. It thanks you for the call, says the team is proceeding
    with candidates whose experience aligns better, and wishes you well. The
    advancement phrase inside it was correctly taken back by the generic-subject
    cue - the sentence is about other people - and then nothing said that this
    is precisely what a decline looks like.
    """
    verdict = rules.classify({
        "subject": "Update on Your Application (Senior Software Engineer, Security)",
        "from_name": "Noora Ali",
        "from_address": "noora@super.example",
        "body": "Thank you again for taking the time to speak with me about the "
                "Senior Software Engineer, Security role.\n\n"
                "After careful consideration, the team has decided to move forward "
                "with candidates whose experience more closely aligns with our "
                "current business needs for this position.\n\n"
                "We wish you all the best in your continued search and future "
                "endeavors.",
    })
    assert verdict["status"] == rules.REJECT
    assert verdict["confidence"] == "high"


def test_moving_forward_with_you_is_still_an_advancement():
    """The object decides it. The rule must not reach the sentence it is the
    mirror image of."""
    for body in ["We have decided to move forward with you to the next round.",
                 "We have decided to move forward with your application."]:
        verdict = rules.classify({
            "subject": "Your application", "from_name": "Contoso",
            "from_address": "no-reply@contoso.example", "body": body})
        assert verdict["reject_score"] == 0, body


# --- Applying to the same role twice -----------------------------------------
#
# Reported from a phone: a receipt from Brex arrived on 2026-10-08 and the list
# went on showing the one from 2026-08-29. The dedup key was employer + role +
# stage and knew nothing about time, so the second receipt was "the same mail
# twice" and was dropped. Three applications in one real mailbox of 335 rows
# had gone that way; the genuine repeats in it were 0 days apart and the
# re-applications 21, 23 and 40.

def _mail(status, date, **kw):
    return {"received": date + "T09:00:00Z", "status": status}


def test_two_receipts_far_apart_are_two_attempts():
    mails = [_mail("Acknowledge", "2026-08-29"), _mail("Acknowledge", "2026-10-08")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 30)
    assert got == [1, 2]


def test_two_receipts_inside_the_window_are_one_attempt():
    """One receipt mailed twice. The stage-level collapse handles the rest."""
    mails = [_mail("Acknowledge", "2026-08-29"), _mail("Acknowledge", "2026-09-10")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 20)
    assert got == [1, 1]


def test_a_late_rejection_belongs_to_the_application_it_rejects():
    """The clause that cost the most to get right. Employers go quiet for
    months and then decline; without "only a receipt starts an attempt" that
    rejection became an application of its own containing nothing else."""
    mails = [_mail("Acknowledge", "2026-01-11"), _mail("Reject", "2026-06-30")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 30)
    assert got == [1, 1]


def test_a_long_application_measured_mail_to_mail_stays_one():
    """Chained, not measured from the start: every stage three weeks after the
    last, running five months, is one application."""
    mails = [_mail("Acknowledge", "2026-01-01"), _mail("Invite to test", "2026-01-22"),
             _mail("Invite to first interview", "2026-02-12"),
             _mail("Reject", "2026-03-05")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 30)
    assert got == [1, 1, 1, 1]


# --- A receipt after a rejection is a new application, whatever the gap ------
#
# Reported from a phone: two Microsoft applications, four mails, and the two
# from October missing from the list. Microsoft declined one requisition on
# 2026-09-24 and acknowledged the next on 2026-10-07 — thirteen days, inside
# any sensible window — so the October receipt was read as the September
# application's receipt arriving twice, and the stage-level collapse in
# `select_rows` then dropped both of October's mails behind the earlier pair.
#
# The window cannot answer this one. Nobody is sent a receipt for an
# application that has already been refused, so the interval carries no
# information: a receipt after a rejection is a new application at thirteen
# days and at one.

def test_a_receipt_after_a_rejection_is_a_new_attempt():
    mails = [_mail("Acknowledge", "2026-07-31"), _mail("Reject", "2026-09-24"),
             _mail("Acknowledge", "2026-10-07"), _mail("Reject", "2026-10-28")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 20)
    assert got == [1, 1, 2, 2]


def test_a_receipt_the_day_after_a_rejection_is_still_a_new_attempt():
    """No window is consulted, so one day is as good as thirteen."""
    mails = [_mail("Acknowledge", "2026-07-31"), _mail("Reject", "2026-09-24"),
             _mail("Acknowledge", "2026-09-25")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 20)
    assert got == [1, 1, 2]


def test_a_rejection_does_not_start_an_attempt_by_itself():
    """Only a receipt starts one. Two rejections for a role that was
    acknowledged once are one application, however far apart — the second is
    a resend, and `select_rows` collapses it."""
    mails = [_mail("Acknowledge", "2026-01-11"), _mail("Reject", "2026-06-30"),
             _mail("Reject", "2026-09-30")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 20)
    assert got == [1, 1, 1]


def test_the_ended_flag_resets_with_the_attempt():
    """Third application after a second rejection, not a third one carried
    over from the first. Off-by-one in the reset would number these 1,1,2,2,4."""
    mails = [_mail("Acknowledge", "2026-01-01"), _mail("Reject", "2026-02-01"),
             _mail("Acknowledge", "2026-02-10"), _mail("Reject", "2026-03-01"),
             _mail("Acknowledge", "2026-03-10")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 20)
    assert got == [1, 1, 2, 2, 3]


def test_the_october_microsoft_pair_reaches_the_report():
    """End to end. Both applications, four rows, two attempts."""
    def ack(id_, date, position):
        return {"id": id_, "received": date + "T09:00:00Z", "date": date,
                "status": "Acknowledge", "company": "Microsoft",
                "position": position, "company_source": "agent",
                "position_source": "agent", "from_address": "x@microsoft.com",
                "subject": "Thank you for your application!"}

    def rej(id_, date, position):
        row = ack(id_, date, position)
        row["status"] = "Reject"
        row["subject"] = "Thank you for your interest in Microsoft"
        return row

    role = "Senior Software Engineer"
    store = {
        "a": ack("a", "2026-07-31", role), "b": rej("b", "2026-09-24", role),
        "c": ack("c", "2026-10-07", role), "d": rej("d", "2026-10-28", role),
    }
    rows = report.select_rows(store, cutoff=None, repeat_window_days=20)
    assert [(r[3]["date"], r[3]["status"], r[4]) for r in rows] == [
        ("2026-07-31", "Acknowledge", 1), ("2026-09-24", "Reject", 1),
        ("2026-10-07", "Acknowledge", 2), ("2026-10-28", "Reject", 2),
    ]


def test_a_second_attempt_is_a_second_application_in_the_report():
    """End to end, through select_rows: two entries, each with one receipt."""
    store = {
        "a": {"id": "a", "received": "2026-08-29T09:00:00Z", "date": "2026-08-29",
              "status": "Acknowledge", "company": "Brex", "position": None,
              "from_address": "no-reply@brex.com", "subject": "Thank you for applying",
              "company_source": "ai", "position_source": "rule"},
        "b": {"id": "b", "received": "2026-10-08T21:05:00Z", "date": "2026-10-08",
              "status": "Acknowledge", "company": "Brex", "position": None,
              "from_address": "no-reply@brex.com", "subject": "Thank you for applying",
              "company_source": "ai", "position_source": "rule"},
    }
    rows = report.select_rows(store, cutoff=None, repeat_window_days=30)
    assert [(r[3]["date"], r[4]) for r in rows] == [("2026-08-29", 1), ("2026-10-08", 2)]
    # And with the rule turned off, the old behaviour exactly.
    rows = report.select_rows(store, cutoff=None, repeat_window_days=3650)
    assert [(r[3]["date"], r[4]) for r in rows] == [("2026-08-29", 1)]


# --- A shared calendar host is not an employer -------------------------------
#
# Reported from a phone: a Google Calendar invitation for a Xero interview,
# which the agent had identified as Xero correctly and stored as Xero, appeared
# in the list under GitLab. Both bookings arrive from
# c_<hash>@group.calendar.google.com, and `canonical_by_domain` - whose job is
# to join "Ea" from ea.com to "Electronic Arts" from a subject line - took that
# host for an employer domain and rewrote every booking in the mailbox to
# whichever employer won it.

def _booking(id_, company, subject, date):
    return {"id": id_, "status": "Invite to first interview", "date": date,
            "received": date + "T10:00:00Z", "company": company,
            "company_source": "agent", "position": "Senior Engineer",
            "position_source": "agent", "from_name": company + " Interviews",
            "from_address": "c_%s@group.calendar.google.com" % id_,
            "subject": subject, "scores": {"reject": 0, "next": 0},
            "explicit_round": None}


def test_two_employers_booking_through_one_calendar_stay_apart():
    store = {
        "a": _booking("a", "GitLab", "Invitation: Your Interview with GitLab", "2026-09-03"),
        "b": _booking("b", "Xero", "Invitation: Recruiter Connect with Xero", "2026-10-07"),
    }
    got = {row["id"]: name for _, name, _, row, _att in report.select_rows(store, None, 20)}
    assert got == {"a": "GitLab", "b": "Xero"}


def test_the_calendar_host_names_no_employer():
    """`company_from_domain` must not answer "Google" for a booking either -
    that is how the first of these rows was stored before the agent corrected
    it."""
    assert rules.company_from_domain("c_abc@group.calendar.google.com") is None


# --- One role written at two lengths -----------------------------------------
#
# Reported on three IBM mails, all for "127308 - Software Engineer - Cloud
# Platform": the receipt carried the specialism and the other two did not, so
# the application was two applications and the timeline broke in half. The
# rules and the agent disagree about how much of a title to keep - the rules
# read one line, the agent reads the whole message - and that disagreement is
# permanent, so the grouping has to absorb it.

def _role(id_, position, status, date):
    return {"id": id_, "status": status, "date": date, "received": date + "T10:00:00Z",
            "company": "Contoso", "company_source": "domain", "position": position,
            "position_source": "rule", "from_address": "careers@contoso.com",
            "from_name": "Contoso Careers", "subject": "About your application",
            "scores": {"reject": 0, "next": 0}, "explicit_round": None}


def test_a_short_title_and_a_long_one_are_one_application():
    store = {
        "a": _role("a", "Software Engineer - Cloud Platform", "Acknowledge", "2026-08-29"),
        "b": _role("b", "Software Engineer", "Invite to test", "2026-08-30"),
        "c": _role("c", "Software Engineer", "Reject", "2026-10-09"),
    }
    rows = report.select_rows(store, None, 20)
    assert len({(r[1], rules.position_key(r[2]), r[4]) for r in rows}) == 1
    # And the specialism is what the row is named, since that is the part the
    # reader applied under.
    assert {r[2] for r in rows} == {"Software Engineer - Cloud Platform"}


def test_a_title_that_could_belong_to_two_longer_ones_is_left_alone():
    """The bare title under an employer that has two specialisms could be
    either, and guessing would merge two real applications."""
    assert rules.fold_position_keys(
        ["Software Engineer", "Software Engineer - Cloud Platform",
         "Software Engineer - Security"]) == {}


def test_a_longer_title_is_not_the_same_role_when_a_word_only_starts_the_same():
    """position_key squashes to letters, which makes "Data Engineer" a prefix
    of "Data Engineering Manager". Those are two jobs."""
    assert rules.fold_position_keys(["Data Engineer", "Data Engineering Manager"]) == {}


# --- The one role still open names the mail that names none ----------------
#
# Reported from a phone, with the list open on four Asana entries called
# "Unknown-1" through "Unknown-4", every one of them labelled a first
# interview. Asana turned down the Software Engineer application in October
# and interviewed for Threat Response in November; the four November mails
# were a hiring-manager invitation and the logistics around it, and none of
# them named a role. `backfill_positions` refuses where an employer has two
# titles - rightly, two is a coin toss - but one of these two had been
# rejected, and the reader could see at a glance which role the mail was
# about.

def _asana(id_, status, date, position=None, subject=None):
    # A subject per message, because mails sharing a thread deliberately stay
    # in one round - give them all the same one and the gap is never consulted.
    return {"id": id_, "status": status, "date": date,
            "received": date + "T10:00:00Z", "company": "Asana",
            "company_source": "agent", "position": position,
            "position_source": "agent" if position else "rule",
            "from_address": "careers@asana.com", "from_name": "Asana",
            "subject": subject or ("About your application " + id_),
            "scores": {"reject": 0, "next": 0}, "explicit_round": None}


def _live(store):
    return rules.live_positions(store, lambda row: row.get("company"))


def test_the_only_role_still_open_is_offered():
    store = {
        "a": _asana("a", "Acknowledge", "2026-08-01", "Software Engineer"),
        "b": _asana("b", "Reject", "2026-10-23", "Software Engineer"),
        "c": _asana("c", "Acknowledge", "2026-11-02", "Senior Security Engineer"),
    }
    assert _live(store) == {"asana": "Senior Security Engineer"}


def test_two_roles_still_open_is_still_a_coin_toss():
    store = {
        "a": _asana("a", "Acknowledge", "2026-08-01", "Software Engineer"),
        "c": _asana("c", "Acknowledge", "2026-11-02", "Senior Security Engineer"),
    }
    assert _live(store) == {}


def test_every_role_turned_down_offers_nothing():
    """Nothing is open, so nothing is inferable - and a mail arriving after
    the last rejection is more likely to be a new application than a
    continuation of a finished one."""
    store = {
        "a": _asana("a", "Acknowledge", "2026-08-01", "Software Engineer"),
        "b": _asana("b", "Reject", "2026-10-23", "Software Engineer"),
    }
    assert _live(store) == {}


def test_the_untitled_scheduling_mail_joins_the_open_application():
    """End to end: four entries called Unknown become stages of the one
    application they were always about."""
    store = {
        "a": _asana("a", "Acknowledge", "2026-08-01", "Software Engineer"),
        "b": _asana("b", "Reject", "2026-10-23", "Software Engineer"),
        "c": _asana("c", "Acknowledge", "2026-11-02", "Senior Security Engineer"),
        "d": _asana("d", "Invite to interview", "2026-11-04", "Senior Security Engineer"),
        "e": _asana("e", "Invite to interview", "2026-11-09",
                    subject="Asana Next Steps: Hiring Manager Meeting"),
        # The logistics around the same meeting, in its thread, so it is that
        # round and not another one.
        "f": _asana("f", "Invite to interview", "2026-11-09",
                    subject="Re: Asana Next Steps: Hiring Manager Meeting"),
    }
    report.assign_interview_rounds(store, 3)
    rows = report.select_rows(store, None, 20)
    assert [(r[2], r[3]["date"], r[3]["status"]) for r in rows] == [
        ("Senior Security Engineer", "2026-11-02", "Acknowledge"),
        ("Senior Security Engineer", "2026-11-04", "Invite to first interview"),
        ("Senior Security Engineer", "2026-11-09", "Invite to second interview"),
        ("Software Engineer", "2026-08-01", "Acknowledge"),
        ("Software Engineer", "2026-10-23", "Reject"),
    ]
    assert not any("Unknown" in r[2] for r in rows)


def test_an_untitled_receipt_does_not_join_the_open_application():
    """A receipt begins an application and nothing else does, so an untitled
    one may be a third application at that employer rather than a stage of the
    open one. Attaching it would merge the newest into the oldest."""
    store = {
        "a": _asana("a", "Acknowledge", "2026-08-01", "Software Engineer"),
        "b": _asana("b", "Reject", "2026-10-23", "Software Engineer"),
        "c": _asana("c", "Acknowledge", "2026-11-02", "Senior Security Engineer"),
        "d": _asana("d", "Acknowledge", "2026-11-09"),
    }
    rows = report.select_rows(store, None, 20)
    assert ("Unknown", "2026-11-09") in [(r[2], r[3]["date"]) for r in rows]


def test_one_title_ever_still_wins_even_once_rejected():
    """`backfill_positions` is consulted first and is unchanged, so nothing
    that used to be filled in stops being filled in."""
    store = {
        "a": _asana("a", "Acknowledge", "2026-08-01", "Software Engineer"),
        "b": _asana("b", "Reject", "2026-10-23", "Software Engineer"),
        "c": _asana("c", "Invite to interview", "2026-11-09"),
    }
    rows = report.select_rows(store, None, 20)
    assert all(r[2] == "Software Engineer" for r in rows)


def test_a_rescheduled_interview_is_not_another_interview():
    """Super.com cancelled an interview on the 25th and sent the new time on
    the 28th, four days after the invitation it replaced. Counted by the gap
    alone that was a second interview, for an application that only ever had
    one and was turned down three days later. The calendar says so itself."""
    store = {
        "a": _asana("a", "Invite to interview", "2026-09-24", "Security Engineer",
                    subject="Next steps with Asana - Security Engineer"),
        # Further out than the gap, so only the calendar's own word for it can
        # keep this in the round it belongs to.
        "b": _asana("b", "Invite to interview", "2026-10-06", "Security Engineer",
                    subject="Updated invitation: Interview with Asana @ Thu Oct 8, 2026"),
    }
    report.assign_interview_rounds(store, 4)
    assert [store[k]["status"] for k in "ab"] == ["Invite to first interview"] * 2


def test_an_interview_booked_on_an_assessment_day_is_that_assessment():
    """Reported from a phone. Xero wrote "we'd like to progress you to the
    technical assessment" at 14:21 and Karat sent the link to book it at
    14:20, a minute earlier. Nine days after the recruiter screen, the gap
    made the second of those a second interview sitting beside the assessment
    it was arranging. Nobody sits two interviews in a day."""
    store = {
        "a": _asana("a", "Invite to interview", "2026-10-07", "Security Engineer",
                    subject="Book your Talent team screen with Asana"),
        "b": _asana("b", "Invite to interview", "2026-10-16", "Security Engineer",
                    subject="Schedule Your Asana Interview"),
        "c": _asana("c", "Invite to test", "2026-10-16", "Security Engineer",
                    subject="Next Steps! Let's Book Your Technical Interview"),
    }
    report.assign_interview_rounds(store, 4)
    assert store["b"]["status"] == "Invite to first interview"
    assert store["c"]["status"] == "Invite to test"
    # And the stage collapse then keeps one row for that first interview.
    rows = report.select_rows(store, None, 20)
    assert [(r[3]["date"], r[3]["status"]) for r in rows] == [
        ("2026-10-07", "Invite to first interview"),
        ("2026-10-16", "Invite to test"),
    ]


def test_an_assessment_on_another_day_does_not_absorb_an_interview():
    """The rule is "the same day", not "anywhere in the history". An
    assessment in October cannot explain away an interview in November."""
    store = {
        "a": _asana("a", "Invite to interview", "2026-10-07", "Security Engineer",
                    subject="Book your Talent team screen with Asana"),
        "b": _asana("b", "Invite to test", "2026-10-16", "Security Engineer",
                    subject="Next Steps! Let's Book Your Technical Interview"),
        "c": _asana("c", "Invite to interview", "2026-11-09", "Security Engineer",
                    subject="Asana Next Steps: Hiring Manager Meeting"),
    }
    report.assign_interview_rounds(store, 4)
    assert store["c"]["status"] == "Invite to second interview"


def test_a_fresh_calendar_invitation_can_still_be_a_new_round():
    """"Invitation:" is a new event; only an *updated* one is the same event
    moved. Reading both the same way would merge two real rounds."""
    store = {
        "a": _asana("a", "Invite to interview", "2026-09-24", "Security Engineer",
                    subject="Next steps with Asana - Security Engineer"),
        "b": _asana("b", "Invite to interview", "2026-10-20", "Security Engineer",
                    subject="Invitation: Interview with Asana @ Thu Oct 22, 2026"),
    }
    report.assign_interview_rounds(store, 4)
    assert [store[k]["status"] for k in "ab"] == [
        "Invite to first interview", "Invite to second interview"]


def test_a_rescheduled_interview_with_nothing_before_it_is_the_first():
    """The clause says "not a new round", which must not mean "round zero"."""
    store = {
        "a": _asana("a", "Invite to interview", "2026-09-28", "Security Engineer",
                    subject="Updated invitation: Interview with Asana @ Thu Oct 1, 2026"),
    }
    report.assign_interview_rounds(store, 4)
    assert store["a"]["status"] == "Invite to first interview"


def test_the_rounds_and_the_rows_agree_on_which_application_a_mail_is_in():
    """They did not. `assign_interview_rounds` grouped on the raw company and
    position; `select_rows` resolved them. A mail that named no role was its
    own group when the rounds were counted, so it could only ever be a first
    interview, and it then joined the real application a step later and was
    swallowed by the first-round row already there."""
    store = {
        "a": _asana("a", "Acknowledge", "2026-11-02", "Security Engineer"),
        "b": _asana("b", "Invite to interview", "2026-11-04", "Security Engineer"),
        "c": _asana("c", "Invite to interview", "2026-11-09",
                    subject="Asana Next Steps: Hiring Manager Meeting"),
    }
    roles = report.resolved_roles(store)
    assert {ident: role for ident, (_, role) in roles.items()} == {
        "a": "Security Engineer", "b": "Security Engineer", "c": "Security Engineer"}
    report.assign_interview_rounds(store, 4)
    assert store["c"]["status"] == "Invite to second interview"


# --- An offer reaches the report ---------------------------------------------
#
# Nothing here classifies an offer: the cost of inventing one out of "we offer
# competitive benefits" is not symmetrical with the cost of missing one. But
# the reader can set it by hand in the iOS app, which shares this store, and
# `CSV_STATUSES` did not list it - so an application that got as far as an
# offer lost that row from applications.csv. The best news in the file, gone
# without a word. Classifying it and reporting it are different questions.

def _offer_store():
    def row(id_, status, date):
        return {"id": id_, "status": status, "date": date,
                "received": date + "T09:00:00Z", "company": "Contoso",
                "company_source": "user", "position": "Senior Engineer",
                "position_source": "user", "from_address": "careers@contoso.com",
                "subject": "About your application",
                "scores": {"reject": 0, "next": 0}, "explicit_round": None}
    return {"a": row("a", "Acknowledge", "2026-08-01"),
            "b": row("b", "Offer", "2026-09-01")}


def test_an_offer_reaches_the_report():
    rows = report.select_rows(_offer_store(), None, 20)
    assert [(r[3]["date"], r[3]["status"]) for r in rows] == [
        ("2026-08-01", "Acknowledge"), ("2026-09-01", "Offer")]


def test_an_offer_is_the_last_stage_not_a_history_running_backwards():
    """An application filed against two requisitions collects a rejection from
    one of them. Ordering the offer before the rejection would report that
    pair as a timeline that runs backwards."""
    store = _offer_store()
    store["c"] = dict(store["b"], id="c", status="Reject", date="2026-09-01",
                      received="2026-09-01T10:00:00Z")
    rows = report.select_rows(store, None, 20)
    assert [r[3]["status"] for r in rows] == ["Acknowledge", "Reject", "Offer"]
    # And it is not reported as a history running backwards.
    import io
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        report.warn_out_of_sequence(rows)
    assert err.getvalue() == ""


def test_nothing_classifies_an_offer():
    """The rules must never claim one. If this fails, a rule has learned to
    say "Offer" and the reason the status exists has been undone."""
    assert rules.OFFER not in {
        status for rule in (rules.REJECT_RULES + rules.TEST_RULES + rules.ACK_RULES)
        for status in (rule if isinstance(rule, str) else ())}


def test_a_receipt_after_an_offer_is_a_new_attempt():
    """Nobody is sent a receipt for the job they have just been offered."""
    mails = [_mail("Acknowledge", "2026-07-31"), _mail("Offer", "2026-09-24"),
             _mail("Acknowledge", "2026-10-07")]
    got = report.attempts([(m["received"], m["status"]) for m in mails], 20)
    assert got == [1, 1, 2]


def test_a_title_the_reader_typed_never_folds_into_a_longer_one():
    """Two live Microsoft requisitions. The reader had opened the October
    application and named the role himself; folding his title into September's
    longer one filed his two October mails under the wrong job - and, because
    the two then shared a group, the attempt rule swallowed them whole."""
    assert rules.fold_position_keys(
        ["Senior Software Engineer", "Senior Software Engineer - Data Integration"],
        typed=["Senior Software Engineer"]) == {}
    # Without the correction it is one role, which is what makes the shorter
    # title's provenance the whole of the difference.
    assert rules.fold_position_keys(
        ["Senior Software Engineer", "Senior Software Engineer - Data Integration"]) != {}


def test_a_typed_title_still_collects_the_longer_spellings_of_itself():
    """Protection is one-way. The reader's title does not fold away, but
    nothing stops a title nobody typed from folding into it."""
    assert rules.fold_position_keys(
        ["Staff Engineer", "Staff Engineer - Payments"],
        typed=["Staff Engineer - Payments"]) == {
            rules.position_key("Staff Engineer"):
                rules.position_key("Staff Engineer - Payments")}


def test_two_requisitions_at_one_employer_stay_two_applications():
    """End to end, the shape that was reported: four mails, two applications,
    each keeping its own name."""
    def row(id_, position, status, date, source):
        r = _role(id_, position, status, date)
        r["position_source"] = source
        return r

    store = {
        "a": row("a", "Senior Software Engineer - Data Integration", "Acknowledge",
                 "2026-07-31", "agent"),
        "b": row("b", "Senior Software Engineer - Data Integration", "Reject",
                 "2026-09-24", "agent"),
        "c": row("c", "Senior Software Engineer", "Acknowledge", "2026-10-07", "user"),
        "d": row("d", "Senior Software Engineer", "Reject", "2026-10-28", "agent"),
    }
    rows = report.select_rows(store, None, 20)
    assert [(r[2], r[3]["date"]) for r in rows] == [
        ("Senior Software Engineer", "2026-10-07"),
        ("Senior Software Engineer", "2026-10-28"),
        ("Senior Software Engineer - Data Integration", "2026-07-31"),
        ("Senior Software Engineer - Data Integration", "2026-09-24"),
    ]


def test_a_requisition_id_is_never_the_best_name_for_a_role():
    store = {
        "a": _role("a", "Sr Software Developer (R0338030)", "Acknowledge", "2026-08-01"),
        "b": _role("b", "Sr Software Developer", "Reject", "2026-09-01"),
    }
    assert {r[2] for r in report.select_rows(store, None, 20)} == {"Sr Software Developer"}


# --- A typed name belongs to the message it was typed for --------------------
#
# Reported: one company with two roles is two applications, and correcting the
# employer on one of them renamed the other too. A typed name outranks
# everything, so it won the domain map - and the domain map is what joins names
# that never fold together, across every application from that sender.

def _at_ibm(id_, position, company, source):
    return {"id": id_, "status": "Acknowledge", "date": "2026-08-%s" % id_,
            "received": "2026-08-%sT10:00:00Z" % id_, "company": company,
            "company_source": source, "position": position, "position_source": "rule",
            "from_address": "talent@ibm.com", "from_name": "IBM",
            "subject": "Thanks for applying", "scores": {"reject": 0, "next": 0},
            "explicit_round": None}


def _named(store):
    return {row["id"]: name for _, name, _, row, _att in report.select_rows(store, None, 20)}


def test_correcting_one_application_leaves_the_others_alone():
    store = {"10": _at_ibm("10", "Software Engineer", "Acme Canada", "user"),
             "11": _at_ibm("11", "Data Engineer", "IBM", "domain")}
    assert _named(store) == {"10": "Acme Canada", "11": "IBM"}


def test_a_spelling_correction_still_spreads():
    """The propagation the edit screen was built for: one employer, one name,
    however its mails spell it."""
    store = {"10": _at_ibm("10", "Software Engineer", "IBM", "user"),
             "11": _at_ibm("11", "Data Engineer", "Ibm", "domain")}
    assert _named(store) == {"10": "IBM", "11": "IBM"}


def test_a_typed_name_is_not_overruled_by_the_domain():
    """The first attempt at this kept a correction from spreading by dropping
    typed names from the domain map, and the domain then renamed the corrected
    row back - losing the correction altogether."""
    store = {"10": _at_ibm("10", "Software Engineer", "Acme Canada", "user")}
    assert _named(store) == {"10": "Acme Canada"}


# --- A name read off a routing address ---------------------------------------
#
# Reported: an Oracle Recruiting acknowledgement for a Fortinet job was filed
# under "Edel Fa Sender", which is the sending tenant. The mail names no
# employer anywhere, so there was nothing for the reader to notice - a
# plausible wrong name invites no doubt at all. In one real mailbox this rule
# fired eight times and was wrong eight times.

def test_webmail_is_not_a_tenant():
    """The reader's own address became the employer "Michael Xuch" twice."""
    assert rules.company_from_ats_localpart("michael.xuch@gmail.com") is None


def test_the_ats_vendor_is_not_the_employer():
    assert rules.company_from_ats_localpart("workday@otp.workday.com") is None
    assert rules.company_from_ats_localpart("greenhouse@mail.greenhouse.io") is None


def test_a_real_tenant_still_names_its_employer():
    """The shape the rule was written for, and the reason it stays."""
    assert rules.company_from_ats_localpart("contoso@myworkday.com") == "Contoso"


def test_a_tenant_guess_is_not_offered_to_the_agent():
    """The agent was handed "Edel Fa" as the rules' guess and agreed with it at
    97%. With no hint it answers from the text, and "not found" is the truthful
    answer a person can then correct."""
    item = rules.review_item(
        {"id": "x", "subject": "s", "from_name": "n", "from_address": "a@b.com"},
        {"position": None, "date": "2026-10-15", "scores": {}},
        {"status": "Acknowledge"}, "Edel Fa Sender", "uncertain", "why",
        company_source="ats-localpart")
    assert item["rule_company"] is None

    kept = rules.review_item(
        {"id": "x", "subject": "s", "from_name": "n", "from_address": "a@b.com"},
        {"position": None, "date": "2026-10-15", "scores": {}},
        {"status": "Acknowledge"}, "Fortinet", "uncertain", "why",
        company_source="text")
    assert kept["rule_company"] == "Fortinet"


# --- Two roles at one employer, and a mail that names neither ----------------
#
# Asked on 小红书 by somebody who had seen it coming: "同一家公司投两个岗位，
# 后续邮件又没写岗位名时，会先留成待确认吗？这种情况宁可暂时空着，也不想被
# 归到错误的申请里。" It did not. It filed the mail under whichever title it
# had seen first.

def _at(id_, position, status, date, company="Contoso"):
    return {"id": id_, "status": status, "date": date, "received": date + "T10:00:00Z",
            "company": company, "company_source": "domain", "position": position,
            "position_source": "rule", "from_address": "careers@contoso.com",
            "from_name": company, "subject": "About your application",
            "scores": {"reject": 0, "next": 0}, "explicit_round": None}


def _roles(store):
    return {r[3]["id"]: r[2] for r in report.select_rows(store, None, 20)}


def test_a_mail_naming_no_role_is_not_filed_under_a_guess():
    store = {"a": _at("a", "Backend Engineer", "Acknowledge", "2026-08-01"),
             "b": _at("b", "Data Scientist", "Acknowledge", "2026-08-02"),
             "c": _at("c", None, "Reject", "2026-09-01")}
    # Not "Backend Engineer", which is what it used to be. Unnumbered because
    # it is the only such message here.
    assert _roles(store)["c"] == "Unknown"


def test_two_such_mails_do_not_become_one_application():
    """Leaving the title blank is not neutral: every one of them becomes
    "Unknown" at that employer and they merge into an invented application. On
    the fixtures that produced a history running interview, then
    acknowledgement."""
    store = {"a": _at("a", "Backend Engineer", "Acknowledge", "2026-08-01"),
             "b": _at("b", "Data Scientist", "Acknowledge", "2026-08-02"),
             "c": _at("c", None, "Reject", "2026-09-01"),
             "d": _at("d", None, "Invite to first interview", "2026-09-05")}
    named = _roles(store)
    assert named["c"] == "Unknown-1"
    assert named["d"] == "Unknown-2"


def test_one_role_is_still_filled_in():
    """The case the backfill was written for: an interview booking names no
    role, and that employer has exactly one."""
    store = {"a": _at("a", "Backend Engineer", "Acknowledge", "2026-08-01"),
             "c": _at("c", None, "Invite to first interview", "2026-09-01")}
    assert _roles(store)["c"] == "Backend Engineer"


def test_the_numbering_is_per_employer_and_in_arrival_order():
    store = {"a": _at("a", "Backend Engineer", "Acknowledge", "2026-08-01"),
             "b": _at("b", "Data Scientist", "Acknowledge", "2026-08-02"),
             "c": _at("c", None, "Reject", "2026-09-05"),
             "x": _at("x", "Platform Engineer", "Acknowledge", "2026-08-01", company="Fabrikam"),
             "y": _at("y", "SRE", "Acknowledge", "2026-08-02", company="Fabrikam"),
             "z": _at("z", None, "Reject", "2026-09-01", company="Fabrikam")}
    for row in ("x", "y", "z"):
        store[row]["from_address"] = "careers@fabrikam.com"
    named = _roles(store)
    assert named["c"] == "Unknown", "one apiece, so neither needs a number"
    assert named["z"] == "Unknown"


def test_an_employer_with_no_known_title_is_left_as_it_was():
    """Karat: two mails, neither naming a role, and nothing to borrow one
    from. They still pool under a single "Unknown" - which has its own fault,
    an entry whose stages run backwards, but it is a different fault and
    splitting it apart turned one entry into four."""
    store = {"a": _at("a", None, "Invite to first interview", "2026-10-16"),
             "b": _at("b", None, "Acknowledge", "2026-10-20")}
    assert set(_roles(store).values()) == {"Unknown"}


def test_one_such_message_needs_no_number():
    """Nothing for it to collide with, and "Unknown" reads better."""
    store = {"a": _at("a", None, "Acknowledge", "2026-08-01")}
    assert _roles(store) == {"a": "Unknown"}
