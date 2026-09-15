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
    assert [(r["date"], r["status"]) for _, _, _, r in rows] == [
        ("2026-08-29", "Acknowledge"),
        ("2026-08-30", "Invite to first interview"),
        ("2026-09-09", "Reject"),
    ]
    # and all three read under one title, the plainest one seen
    assert {position for _, _, position, _ in rows} == {"Senior Backend Engineer"}


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
    assert {(name, position) for _, name, position, _ in rows} == {
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
    assert {name for _, name, _, _ in rows} == {"Thales"}


@pytest.mark.parametrize("a,b,same", [
    ("Thalesgroup", "Thales", True),
    ("D-Wave Quantum Inc.", "D-Wave Quantum", True),
    ("Coalitioninc", "Coalition", True),
    ("Zinc", "Z", False),
])
def test_corporate_suffix_is_not_a_different_employer(a, b, same):
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
    names = {name for _, name, _, _ in report.select_rows(store, cutoff=None)}
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
    assert (verdict["reject_score"], verdict["next_score"]) == (35, 14)
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
