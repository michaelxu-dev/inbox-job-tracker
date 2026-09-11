"""Reading one email and deciding what it means for an application.

Reject / Acknowledge / Invite to test / Invite to interview / Unclear.

The hard part is not spotting the words but distrusting them. "We are unable to
move forward with your application" contains a textbook advancement phrase and is
a rejection; "successful candidates move on to a video interview" describes a
process rather than offering anything; a layoff letter is worded exactly like a
rejection but answers no application at all. So a match is only counted once its
surroundings agree with it, and several gates run before any scoring at all.

Anything the rules cannot call confidently is queued for review instead of
guessed at - see inboxjobtracker/judge.py.
"""
import csv
import datetime as dt
import json
import os
import re
import sys

from .prefilter import ATS_DOMAINS as ATS_PLATFORMS

REJECT = "Reject"
ACK = "Acknowledge"
TEST = "Invite to test"
UNCLEAR = "Unclear"
# Mail almost never states which round it is, so classify() emits this generic
# marker and assign_interview_rounds() resolves it into an ordinal from the
# chronological order of invitations for that employer + role.
INTERVIEW = "Invite to interview"
INTERVIEW_ROUNDS = ["Invite to first interview", "Invite to second interview",
                    "Invite to third interview"]
# Statuses that reach the CSV. Unclear (job alerts, newsletters, cold pitches)
# never does.
CSV_STATUSES = frozenset([REJECT, ACK, TEST] + INTERVIEW_ROUNDS)
# The order an application moves through. Used to sort same-day stages sensibly
# and to flag a history that runs backwards.
STAGE_ORDER = {ACK: 0, TEST: 1, INTERVIEW_ROUNDS[0]: 2, INTERVIEW_ROUNDS[1]: 3,
               INTERVIEW_ROUNDS[2]: 4, REJECT: 5}
# Superseded spellings, mapped on load so an existing store keeps working.
LEGACY_STATUS = {"Pass to next round": INTERVIEW, "Acknowledgement": ACK}

# Weighted so an explicit decision outranks incidental wording.
REJECT_RULES = [
    (10, r"we (regret|are sorry) to inform"),
    # Every part of this varies in real mail: "not moving forward with your
    # application", "decision to not move forward for your application",
    # "will not be going forward on your candidacy". Pinning the verb to
    # "moving|going" and the preposition to "with" missed an outright decline
    # and left it scoring 4 on a sign-off phrase alone.
    (10, r"not (be )?(mov(e|es|ing)|go(es|ing)?|proceed(s|ing)?|"
         r"continu(e|es|ing)?) forward (with|for|on|in) your "
         r"(application|candidacy)"),
    (10, r"(decided|chosen) to (move forward|proceed|continue) with (other|another)"),
    (10, r"you (have not|were not) (been )?(selected|shortlisted|chosen)"),
    (10, r"no longer (be )?under consideration"),
    (10, r"will not be (progressing|proceeding|advancing)"),
    (9, r"your application (was|has been) (unsuccessful|declined)"),
    (9, r"pursu\w+ other candidates"),
    (8, r"not (a|the right) (match|fit) (for|at) this time"),
    (8, r"(decided|(have |has )?(made|reached) (the|a) decision) "
         r"(not to|to not) (proceed|move forward|continue|advance)"),
    (8, r"(unable|not able) to (move forward|proceed|continue|progress)"),
    (8, r"filled (the|this) (position|role)"),
    (6, r"keep your (resume|application|details) on file"),
    (5, r"\bunfortunately\b"),
    (4, r"other candidates whose (qualifications|experience)"),
    (4, r"wish you (the best|success|luck)"),
]

NEXT_RULES = [
    (10, r"(would|we.?d) like to invite you"),
    (10, r"invite you to (an?|the) (interview|assessment|next)"),
    (10, r"(schedule|set ?up|arrange|book) (a|an|your) (call|chat|interview|screen|meeting|time)"),
    (10, r"move (you )?forward (with|in) (your |the )?(application|process|candidacy)"),
    (9, r"(phone|video|initial|technical|final) (screen|screening|interview)"),
    (9, r"next (round|stage)"),
    (9, r"hiring manager would like"),
    (8, r"(please )?(complete|take) (the|an|your) (online |technical |coding )?assessment"),
    (8, r"(share|provide|let us know) your availability"),
    (8, r"(pleased|excited|happy) to (inform|let you know|share).{0,60}(next|interview|advance|progress)"),
    # Needs the active voice. Bare "selected to move forward" is nearly always
    # the passive half of a condition — "if you are selected to move forward,
    # we'll be in touch" — which promises nothing.
    (7, r"we (have |'ve )?selected you (to|for) (move|advance|continue|the next)"),
    (7, r"advance(d)? to the next"),
    (6, r"\bnext steps?\b"),
    (5, r"looking forward to (speaking|meeting|chatting)"),
]

# An advancement is either a test or an interview. Both sets are scored and the
# higher wins, because an assessment invite often mentions interviews in passing
# ("if one of the assessments is a video interview...") and vice versa.
TEST_RULES = [
    (10, r"(assessment|test) invitation"),
    (10, r"(invite|invitation).{0,40}(complete|take).{0,30}(assessment|test)"),
    (10, r"(coding|online|technical|skills?|take[- ]home|pre[- ]?employment) (assessment|test|challenge|exercise)"),
    (10, r"\b(hackerrank|codility|codesignal|karat|coderpad|testgorilla)\b"),
    (8, r"(please )?(complete|take) (the|an|your) (online |technical |coding )?(assessment|test)"),
    (6, r"\bassessment\b"),
]
INTERVIEW_RULES = [
    (10, r"(would|we.?d) like to invite you"),
    (10, r"invite you to (an?|the) (interview|next|following)"),
    (10, r"(schedule|set ?up|arrange|book) (a|an|your) (call|chat|interview|screen|meeting|time)"),
    (9, r"(phone|video|initial|technical|final|onsite) (screen|screening|interview)"),
    (9, r"hiring manager would like"),
    (8, r"(share|provide|let us know) your availability"),
    (7, r"(exploratory|introductory) (call|chat|conversation)"),
    (6, r"\binterview\b"),
]
# Explicit wording beats chronological order and resets the counter.
ROUND_CUES = [
    # "onsite" alone is a work arrangement, not a round: a contract posting
    # saying "hybrid, 3 days onsite at our client's office" was read as a final
    # round, and the reader's only interview with that employer became a third.
    # It counts when it is the interview that is onsite.
    (3, r"\b(final (round|interview|stage)|third (round|interview)|"
        r"on-?site (interview|round|loop|visit)|"
        r"interview on-?site)\b"),
    # "next round"/"next stage" is NOT a round-2 cue: "we'd like to invite you to
    # the next stage of the interview process" is how a first interview is
    # offered. Only wording that names the round counts.
    (2, r"\b(second (round|interview)|2nd (round|interview))\b"),
    (1, r"\b(first (round|interview)|initial (screen|screening|call|interview)|"
        r"phone screen|recruiter screen)\b"),
]

# A NEXT pattern can appear verbatim inside a rejection ("unable to move forward
# with your application") or inside a hypothetical in a plain receipt ("if we
# decide to move forward with your application"). Only the run-up tells them
# apart, so a hit preceded by one of these cues in the same sentence is dropped.
# The cues stay narrow on purpose - a bare "not"/"if" would also kill real
# invitations ("do not hesitate to schedule a call", "if you are available") -
# but the reach is the whole sentence. It used to be the preceding 40
# characters, and "If your application is a good fit for the position, one of
# our team members will contact you to schedule a call" puts 96 between the
# condition and the advancement, so that receipt was published as a first
# interview.
NEGATION_CUE = re.compile(
    r"\b(unable|not able|cannot|can'?t|won'?t|will not|"
    r"regret|declin\w+|unsuccessful|no longer|not (be )?(moving|going|proceeding)|"
    r"not to)\b"
    r"[^.!?;\n]*$", re.I,
)
CONDITIONAL_CUE = re.compile(
    r"\b(if|should|when|once)\s+(we|the (team|recruiter|hiring team)|your application)\b"
    r"[^.!?;\n]*$"
    r"|\bif you (are|were) (selected|chosen|shortlisted)\b[^.!?;\n]*$"
    # The subject of the condition is often dropped or stands in for the
    # application: "If selected, you can expect to hear from us to schedule an
    # initial interview", "If it aligns with what we're looking for, we'll
    # reach out to schedule an interview". Both were read as invitations.
    r"|\bif (selected|chosen|shortlisted|successful|there (is|s)? (a )?(match|fit))\b"
    r"[^.!?;\n]*$"
    r"|\bif (it|this|that|your (background|experience|profile|skills|"
    r"qualifications|resum[eé]|cv))\b[^.!?;\n]*$"
    # A condition about what the reader might observe, rather than about their
    # candidacy: "If you see the job moved to an inactive state, that means the
    # position is no longer open, you withdrew, or you were not selected." That
    # is a legend for a dashboard, and it rejected nobody. Kept to observing and
    # to the job's own state, so "if you are available Thursday" is untouched.
    r"|\bif (you (see|notice|find|do not hear|don'?t hear|have not heard|"
    r"haven'?t heard)|the (job|position|role|req\w*|status|posting)\s+"
    r"(is|was|has been|moves?|moved|becomes?|appears?))\b[^.!?;\n]*$"
    r"|\bin the event\b[^.!?;\n]*$", re.I,
)
# An advancement named as what has *not* happened yet is a request for
# paperwork, not an offer: "I need a few items from you before I can move
# forward with your application" is an agency asking for references. The
# advancement is the thing being withheld until the reader acts.
# "Best wishes for your next steps" is a sign-off. It closes rejections and, in
# the mail that made this rule, a government satisfaction survey - which was
# published as a first interview on the strength of that farewell alone.
VALEDICTION_CUE = re.compile(
    r"\b(best wishes|wish(ing)? you|good luck|all the best|"
    r"every success|the best)\b[^.!?;\n]*$", re.I,
)
# A stage named as something the process *may* contain is a description of the
# process, not an offer of it: "For Engineering roles, this may also include a
# Technical Interview", "you might be asked to complete a skills test". Kept to
# the verbs that describe composition - "you may schedule a call at your
# convenience" is a real invitation and must stay one.
POSSIBILITY_CUE = re.compile(
    r"\b((may|might|could|can) (also )?(include|involve|consist|require|be|contain)|"
    r"depending on|typically|usually|generally|normally)\b"
    r"[^.!?;\n]*$", re.I,
)
PRECONDITION_CUE = re.compile(
    r"\b(before|until|in order (for us )?to|so that (we|i) can|"
    r"pending)\b[^.!?;\n]*$", re.I,
)
# An advancement described about candidates in general is the company explaining
# its process, not an offer: "successful candidates move on to a video
# interview". The giveaway is the third person — nothing has been offered to the
# reader. "other candidates" is already a rejection cue and is left alone.
GENERIC_SUBJECT_CUE = re.compile(
    r"\b(successful|shortlisted|selected|qualified|suitable)?\s*candidates\b"
    r"[^.!?;\n]*$", re.I,
)

# Pure auto-acknowledgements: a response, but not a decision either way.
# Written loosely on purpose. Real mail says "Thanks for applying", "we've
# received", "our teams will review" — an exact "thank you for" / "we have
# received" / "team will review" misses all three in a single message.
APOS = r"['‘’`]"
ACK_RULES = [
    r"thank(s| you)?( so much| you very much)? for (your interest|applying|"
    r"your application|submitting|taking the time)",
    r"we\s*(%s?(ve|re)|have|had)?\s*(recently\s*)?receiv\w* your application" % APOS,
    r"confirming .{0,20}receiv\w* your application",
    r"your application (has been|was|is) (received|submitted|under review|in review)",
    r"application (has been )?(successfully )?(received|submitted)",
    r"we('?ll| will) (review|be reviewing)",
    r"(our|the) (team|recruiter|hiring team|recruiting team)s? (will|are going to) "
    r"(review|be reviewing|take a look|reach out)",
]

# Domains that belong to the ATS vendor or a mail provider, never the employer.
ATS_DOMAINS = {
    "myworkday.com", "workday.com", "greenhouse.io", "lever.co", "hire.lever.co",
    "icims.com", "taleo.net", "oraclecloud.com", "smartrecruiters.com", "ashbyhq.com",
    # Vendor mail/scheduling domains that are NOT just the vendor's main domain —
    # each of these shipped an employer's mail and was read as the employer.
    "greenhouse-mail.io", "kula.ai", "modernloop.io",
    "successfactors.com", "jobvite.com", "workable.com", "breezy.hr", "bamboohr.com",
    "recruitee.com", "teamtailor.com", "avature.net", "brassring.com", "silkroad.com",
    "jazzhr.com", "applytojob.com", "eightfold.ai", "phenompeople.com", "paylocity.com",
    "dayforcehcm.com", "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com",
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "googlemail.com",
}
SUBDOMAIN_NOISE = re.compile(
    r"^(e?mail|mailer|smtp|mx|notification|notifications|notify|reply|no-?reply|"
    r"careers?|jobs?|recruiting|talent|hr|apply|hire|hiring|info|em|mkt|marketing|"
    r"send|sender|bounce|us|eu|na)\.", re.I,
)
NAME_NOISE = re.compile(
    r"\b(careers?|recruit(ing|ment|er)?|talent acquisition|talent|hiring team|"
    r"human resources|hr team|hr|no-?reply|do-?not-?reply|notifications?|team|"
    r"via workday|workday|greenhouse|lever|icims|taleo|smartrecruiters|myworkday)\b",
    re.I,
)


ADVANCEMENT_CUES = (NEGATION_CUE, CONDITIONAL_CUE, GENERIC_SUBJECT_CUE,
                    PRECONDITION_CUE, VALEDICTION_CUE, POSSIBILITY_CUE)
# A rejection is a negation and ends in a farewell, so those two cues cannot be
# applied to it - they are what it is made of. What still holds is that a
# hypothetical rejection rejects nobody: "If you see the job moved to an
# inactive state, that means the position is no longer open, you withdrew, or
# you were not selected" explains a dashboard. It is the one sentence in an
# acknowledgement that reads like a decision, and it was read as one.
REJECT_CUES = (CONDITIONAL_CUE, GENERIC_SUBJECT_CUE)


def discounted(before, cues=ADVANCEMENT_CUES):
    """True when the run-up to a match takes it back: a negation, a
    hypothetical, a description of other candidates, a precondition the reader
    has still to meet, or a parting good wish."""
    return any(cue.search(before) for cue in cues)


def score(text, rules, guarded=False, cues=ADVANCEMENT_CUES):
    """Sum the weights of every rule that fires. With `guarded`, a match whose
    run-up is a negation or a hypothetical does not count — one unguarded
    occurrence anywhere in the text is still enough to score the rule. `cues`
    says which run-ups count, because what discounts an advancement is not what
    discounts a rejection."""
    total, hits = 0, []
    for weight, pattern in rules:
        for m in re.finditer(pattern, text, re.I):
            before = text[:m.start()]
            if guarded and discounted(before, cues):
                continue
            total += weight
            hits.append(pattern)
            break
    return total, hits


def heaviest(rule_table, hits):
    """The weight of the strongest rule that fired."""
    weights = {pattern: weight for weight, pattern in rule_table}
    return max((weights[h] for h in hits), default=0)


def sentence_around(text, start, end):
    """Widen a match out to the sentence holding it."""
    left = max(text.rfind(ch, 0, start) for ch in ".!?\n")
    ends = [p for p in (text.find(ch, end) for ch in ".!?\n") if p != -1]
    right = min(ends) + 1 if ends else len(text)
    return re.sub(r"\s+", " ", text[left + 1:right]).strip(" .,-–—")


def evidence_sentence(subject, body, status):
    """The sentence a decision rests on, for the Notes column: the highest
    weighted rule that fired for the status this mail was given."""
    if status == REJECT:
        rules = REJECT_RULES
    elif status == TEST:
        rules = TEST_RULES
    elif status == ACK:
        rules = [(1, pattern) for pattern in ACK_RULES]
    else:
        rules = INTERVIEW_RULES
    text = "%s\n%s" % (subject or "", body or "")
    best = (0, None)
    for weight, pattern in rules:
        if weight <= best[0]:
            continue
        for m in re.finditer(pattern, text, re.I):
            before = text[:m.start()]
            # Same guard as scoring, so Notes never quotes a sentence that was
            # discounted as hypothetical - including for a rejection, whose
            # own quote used to be exempt and so could cite the dashboard
            # sentence that never rejected anyone.
            if discounted(before, REJECT_CUES if status == REJECT else ADVANCEMENT_CUES):
                continue
            best = (weight, sentence_around(text, m.start(), m.end()))
            break
    sentence = best[1]
    if sentence and len(sentence) > 220:
        sentence = sentence[:217].rstrip() + "..."
    return sentence


def advancement_kind(text):
    """Split an advancement into a test invite or an interview invite."""
    test_score, _ = score(text, TEST_RULES, guarded=True)
    interview_score, _ = score(text, INTERVIEW_RULES, guarded=True)
    return TEST if test_score > interview_score else INTERVIEW


def explicit_round(text):
    """The round number the mail states outright, if it states one."""
    for number, pattern in ROUND_CUES:
        if re.search(pattern, text, re.I):
            return number
    return None


# The decision rules key on phrases that are not unique to hiring: a bank's
# customer survey says "we would like to invite you", a government form's subject
# is "Home Owner Grant Application". Nothing reaches the CSV without some anchor
# tying it to a job, or an ATS platform as the sender. Bare "application" is not
# an anchor, precisely because that grant form has one.
JOB_CONTEXT = re.compile(
    r"\b(job|career|employment|recruit\w*|hiring|talent acquisition|"
    r"candidat\w+|applicant|resum[eé]|cv|cover letter|"
    r"position|role|vacancy|opening|req(uisition)?\s*id|"
    r"interview|screening|assessment|hiring manager|"
    r"applied (for|to)|application (for|to)|your application (for|to|status)|"
    r"thank you for (applying|your application))\b",
    re.I,
)


# A reminder for something already booked. It is not a receipt and not a new
# invitation — the invitation that scheduled it is already recorded — so counting
# it either way is wrong: as an invite it invents an extra interview round, as an
# acknowledgement it invents a second application receipt.
# Matched against the SUBJECT only. A reminder says so in its subject line,
# while the body of a real invitation happily talks about "your upcoming
# interview" — scanning the body turns a genuine second-round invite into noise.
EVENT_REMINDER = re.compile(
    r"^\s*(re:\s*)?(reminder|friendly reminder)\b|"
    r"\breminder\s*[:\-]|"
    r"\bupcoming (interview|assessment|call|meeting|screen)\b|"
    r"\b(interview|call|meeting) (is|starts|begins) (tomorrow|today|in \d)",
    re.I,
)

# Transactional mail from the ATS itself: confirm your address, activate your
# candidate account, here is a sign-in code. It arrives from a real ATS domain
# and is often subjected "Thank you for your interest", so both the ack rules
# and the job-context gate pass it — but nothing about an application is being
# answered. Checked on purpose against the *reason* for the mail, not the words
# around it, so a receipt that merely mentions a candidate account is unaffected.
ACCOUNT_ADMIN = re.compile(
    r"(confirm (your )?e-?mail( address)?|verify (your )?e-?mail( address)?|"
    r"activate (your )?(candidate |applicant )?account|"
    r"(set ?up|setting up|create|creating|register) (your |an |a )?[\w ]{0,20}account|"
    r"complete setup for your (candidate|applicant) account|"
    r"(this )?link will expire|"
    r"one[- ]time (passcode|password|code)|\bOTP\b|access code|sign-?in code|"
    r"reset your password|password reset)",
    re.I,
)

# A recruiter mailing from personal webmail is not an employer's recruiting
# system. Real outreach comes from a company domain or an ATS; these are
# unverifiable at best and phishing at worst, so they never reach the CSV.
PERSONAL_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.ca", "yahoo.co.uk",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "protonmail.com", "proton.me",
    "mail.com", "gmx.com", "gmx.net", "yandex.com", "qq.com", "163.com",
    "126.com", "foxmail.com", "zoho.com",
}


# Job boards and aggregators advertise openings; they do not answer
# applications. Their marketing dominates their volume by a wide margin - one
# 60-day Gmail scan queued 21 Glassdoor digests and adverts and not one real
# reply - and every one of them mentions jobs, so the job-context gate below
# waves them all through. They are listed as ATS platforms for the prefilter's
# purposes, which is right: the prefilter should be generous. Deciding is not.
# A real decision still wins, because this gate yields to a confident score.
JOB_BOARDS = {
    "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com",
    "monster.com", "dice.com", "simplyhired.com", "careerbuilder.com",
    "seek.com.au", "totaljobs.com", "reed.co.uk", "jobbank.gc.ca",
}


def is_job_board(address):
    domain = (address or "").lower().rsplit("@", 1)[-1].strip("<> ")
    return any(domain == d or domain.endswith("." + d) for d in JOB_BOARDS)


def is_personal_mail(address):
    domain = (address or "").lower().rsplit("@", 1)[-1].strip("<> ")
    return domain in PERSONAL_MAIL_DOMAINS


# Mail about a job the user already holds, not an application for one. A layoff
# letter is worded exactly like an application rejection — "We regret to inform
# you that ... terminate your employment", "we wish you the best" — and scores as
# a confident Reject otherwise. Job-related is not the same as application-related.
EMPLOYMENT_LIFECYCLE = re.compile(
    r"(terminat(e|es|ed|ing|ion) (of )?(your )?employment|notice of termination|"
    r"end(ing)? (of )?your employment|your last day|final pay(check|cheque)|"
    r"severance|laid off|lay(ing)? off|redundanc(y|ies)|"
    r"offboarding|exit interview|resignation)",
    re.I,
)


# Employment insurance, welfare-to-work programmes and the job boards that
# governments run alongside them are about *getting* work, not an answer from an
# employer. They are unusually good at tripping the rules: every one of them
# talks about jobs, applications and next steps, which is exactly what the
# job-context gate looks for. "Your next step after applying for Employment
# Insurance is to set up your Job Bank account" scored as an interview.
BENEFITS_PROGRAMME = re.compile(
    r"\b(employment insurance|\bEI\b (application|claim|benefits?)|"
    r"applied for EI|unemployment (insurance|benefits?)|jobseeker'?s allowance|"
    r"social assistance|income support|"
    r"service canada|job bank|workbc|employment services)\b",
    re.I,
)


def looks_job_related(cand, text):
    """Is this hiring correspondence at all? An ATS platform sender settles it;
    otherwise the text has to mention something about a job."""
    address = (cand.get("from_address") or "").lower()
    domain = address.split("@")[-1] if "@" in address else ""
    if domain and not is_job_board(address) and any(
            domain == d or domain.endswith("." + d) for d in ATS_PLATFORMS):
        return True
    return bool(JOB_CONTEXT.search(text))


def is_own_address(address, own):
    """Mail the user sent — a note to self, or their own reply in a thread. It
    can read exactly like an HR mail (a saved interview script, a reply quoting
    the recruiter) but is never an employer's response, so it never counts."""
    return (address or "").strip().lower().strip("<>") in own


def looks_like_ack(text):
    return any(re.search(p, text, re.I) for p in ACK_RULES)


def company_from_domain(address):
    if "@" not in address:
        return None
    domain = address.split("@")[-1].lower().strip(">.")
    domain = SUBDOMAIN_NOISE.sub("", domain)
    if domain in ATS_DOMAINS or any(domain.endswith("." + d) for d in ATS_DOMAINS):
        return None
    parts = domain.split(".")
    if len(parts) < 2:
        return None
    # co.uk / com.au style suffixes need one more label.
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac"):
        core = parts[-3]
    else:
        core = parts[-2]
    if core in ("mail", "email", "smtp", "amazonses", "sendgrid", "mailgun"):
        return None
    return core.replace("-", " ").title()


def company_from_name(display_name):
    # Some senders set the display name to the address itself. Stripping vendor
    # noise out of that leaves debris ("no-reply@us.greenhouse-mail.io" ->
    # "us. mail.io") which would otherwise beat the real name in the subject.
    if re.search(r"@|\.(com|net|org|io|ai|co|jobs|hr)\b", display_name or "", re.I):
        return None
    cleaned = NAME_NOISE.sub("", display_name or "")
    cleaned = re.sub(r"[|@()\[\]<>,:\-–—]+", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .")
    if len(cleaned) < 2 or re.fullmatch(r"[\W\d_]+", cleaned):
        return None
    # A personal name ("Jane Smith") is a recruiter, not the employer.
    if re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+", cleaned):
        return None
    return cleaned


def company_from_text(subject, body):
    patterns = [
        r"(?:applying|application) (?:to|at|with|for a position at)\s+([A-Z][\w&.\- ]{1,40}?)(?:[,.!\n]|$)",
        r"your (?:interest in|application (?:to|at|with))\s+([A-Z][\w&.\- ]{1,40}?)(?:[,.!\n]|$)",
        r"(?:position|role|opportunity) (?:at|with)\s+([A-Z][\w&.\- ]{1,40}?)(?:[,.!\n]|$)",
        r"\bat\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s*(?:!|\.|,|\n)",
    ]
    for source in (subject or "", body or ""):
        for pattern in patterns:
            match = re.search(pattern, source)
            if match:
                value = re.sub(r"\s{2,}", " ", match.group(1)).strip(" .,")
                # "Thank you in your interest in Senior Software Developer"
                # hands back the role, not the employer. A job title is never a
                # company name, so keep looking.
                if re.search(JOB_WORD, value, re.I):
                    continue
                if 1 < len(value) <= 45:
                    return value
    return None


# Job titles are anchored on a role noun and grown outwards: optional seniority,
# a few capitalised qualifiers, an optional level ("II"), an optional trailing
# specialisation after a comma ("Software Development Engineer II, AWS IAM").
SENIORITY = (r"(?:Senior|Sr\.?|Junior|Jr\.?|Staff|Principal|Lead|Head|Chief|"
             r"Associate|Assistant)")
JOB_WORD = (r"(?:Engineer|Developer|Scientist|Analyst|Manager|Architect|Designer|"
            r"Administrator|Specialist|Consultant|Programmer|Technician|Director|"
            r"Intern|Officer|Coordinator|Researcher)")
TITLE_WORD = r"[A-Z][\w.+/&'-]*"
TITLE_RE = re.compile(
    r"\b((?:" + SENIORITY + r"\s+)?(?:" + TITLE_WORD + r"[ -]){0,4}" + JOB_WORD +
    r"(?:\s+(?:I{1,3}|IV|V|\d))?"
    r"(?:,\s*" + TITLE_WORD + r"(?:[ &/-]+" + TITLE_WORD + r"){0,4})?"
    r"(?:\s*\([^)]{1,28}\))?)"
)
# A trailing requisition id ("(R0338030)") is noise; a real qualifier ("(.Net)")
# is part of the title. Digits are what separate them.
REQ_ID = re.compile(r"\s*\(\s*(?=[^)]*\d)[A-Z]{0,3}[\d\s#-]{3,}\s*\)\s*$")


# A job title in the mail is not always the job applied for: "One of our Talent
# Acquisition Specialists will be in touch" and the recruiter's own signature
# ("Casey Bewley, Talent Acquisition Manager") both name the employer's staff.
# A possessive is no help in telling them apart — "applying to our Software
# Engineer, Developer Platform" is the applied role — but a recruiting title
# never is one, and that alone is enough.
RECRUITER_TITLE = re.compile(
    r"^(talent acquisition|recruit(ing|ment|er)|hr\b|human resources|hiring)", re.I)


def position_from_text(subject, body):
    for source in (subject or "", (body or "")[:1500]):
        for match in TITLE_RE.finditer(source):
            value = re.sub(r"\s{2,}", " ", match.group(1)).strip(" .,-–—")
            value = REQ_ID.sub("", value)
            value = re.sub(r"\s+(Position|Role|Opening)$", "", value, flags=re.I)
            value = value.strip(" .,-–—")
            if not value or RECRUITER_TITLE.match(value):
                continue
            return value
    return None


def name_key(name):
    """Fold spelling differences that mean the same employer: 'Gitlab'/'GitLab',
    and 'Contosolabs' (titlecased from a domain, which cannot know where
    the word break goes) vs 'Contoso Labs'."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


# Where an ATS breaks a title into parts: "Senior Backend Engineer, AMER -
# Evergreen". A bare hyphen is not a separator ("Full-Stack"), only a spaced one.
POSITION_SEPARATOR = re.compile(r"\s*[,|·]\s*|\s+[-–—]\s+|\s*[()\[\]]\s*")

# Decoration an ATS appends to a title: the region the requisition is posted in,
# the always-open "evergreen" pipeline, the work arrangement, the employment
# type, a requisition number. The same application carries different ones in its
# acknowledgement, its interview invitation and its rejection, so folding on the
# raw string files one application under three roles.
POSITION_NOISE = re.compile(
    r"(amer|emea|apac|latam|noram|north america|united states|us|usa|canada|uk|"
    r"remote|hybrid|on-?site|in-?office|work from home|"
    r"evergreen( requisition| pipeline)?|"
    r"full[- ]?time|part[- ]?time|permanent|contract(or)?|temporary|"
    r"req(uisition)?[.:#\s]*\d+|#?\d{3,})")


def strip_position_noise(position):
    """Drop the trailing decoration, keeping everything that names the job.

    Only whole trailing segments go, and only ones made entirely of decoration —
    'Senior Security Engineer, AI Security' is a specialism, not a region, and
    stays. The first segment is always the title, so it is never dropped.
    """
    parts = [p.strip() for p in POSITION_SEPARATOR.split(position or "") if p and p.strip()]
    while len(parts) > 1 and POSITION_NOISE.fullmatch(parts[-1].casefold()):
        parts.pop()
    return ", ".join(parts)


def position_key(position):
    """Fold spellings of the same title: 'Sr Software Developer' and
    'Senior Software Developer' are one role, not two — and so are
    'Senior Backend Engineer' and 'Senior Backend Engineer, AMER - Evergreen'."""
    key = strip_position_noise(position).casefold()
    key = re.sub(r"\bsr\.?\b", "senior", key)
    key = re.sub(r"\bjr\.?\b", "junior", key)
    return re.sub(r"[^a-z0-9]", "", key)


def backfill_positions(store):
    """Scheduling and reminder mail ("Your interview with GitLab is scheduled")
    names no role. Reuse a title seen in any other mail from that employer."""
    by_company = {}
    for row in store.values():
        if row.get("position") and row.get("company"):
            by_company.setdefault(name_key(row["company"]), row["position"])
    return by_company


def restore_casing(name, text):
    """Display names arrive in whatever case the sender used ("workday
    contoso"). If the same name appears in the mail itself, take that
    spelling — that is the company writing its own name, so "ConToso" wins
    over "contoso". Failing that, title-case an all-lowercase name."""
    if not name:
        return name
    match = re.search(re.escape(name), text or "", re.I)
    if match and match.group(0) != name and not match.group(0).islower():
        return match.group(0)
    return name.title() if name.islower() else name


GENERIC_LOCALPART = re.compile(
    r"^(no-?reply|do-?not-?reply|noreply|reply|notifications?|notify|mail(er)?|"
    r"info|support|help|careers?|jobs?|recruiting|recruitment|talent|hr|hiring|"
    r"apply|candidate|help\.candidate|talentcentral|team|admin|alerts?)$", re.I)


def company_from_ats_localpart(address):
    """An ATS tenant address names its employer in the local part —
    contoso@myworkday.com, fabrikam@myworkday.com. Used only as a last
    resort, so a real display name or body mention still wins."""
    if "@" not in (address or ""):
        return None
    local, domain = address.lower().rsplit("@", 1)
    domain = SUBDOMAIN_NOISE.sub("", domain)
    if not (domain in ATS_DOMAINS or any(domain.endswith("." + d) for d in ATS_DOMAINS)):
        return None
    local = local.split("+")[0]
    if GENERIC_LOCALPART.match(local) or len(local) < 3:
        return None
    return local.replace("-", " ").replace(".", " ").title()


def guess_company(cand):
    for guess, source in (
        (company_from_domain(cand["from_address"]), "domain"),
        (company_from_name(cand["from_name"]), "sender-name"),
        (company_from_text(cand["subject"], cand.get("body", "")), "text"),
        (company_from_ats_localpart(cand["from_address"]), "ats-localpart"),
    ):
        if guess:
            text = "%s\n%s" % (cand.get("subject", ""), cand.get("body") or "")
            return restore_casing(guess, text), source
    return None, "unknown"


def review_item(cand, record, verdict, company, reason, why):
    return {
        "id": cand["id"],
        "reason": reason,
        "rule_status": verdict["status"],
        "rule_company": company,
        "rule_position": record["position"],
        "why_review": why,
        "date": record["date"],
        "subject": cand["subject"],
        "from_name": cand["from_name"],
        "from_address": cand["from_address"],
        "scores": record["scores"],
        "body": (cand.get("body") or cand.get("preview", ""))[:1500],
    }


def local_date(iso_utc):
    stamp = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return stamp.astimezone().strftime("%Y-%m-%d")


def classify(cand):
    text = "%s\n%s" % (cand.get("subject", ""), cand.get("body") or cand.get("preview", ""))
    rej, rej_hits = score(text, REJECT_RULES, guarded=True, cues=REJECT_CUES)
    nxt, nxt_hits = score(text, NEXT_RULES, guarded=True)
    ack = looks_like_ack(text)

    # A rejection needs one phrase that actually declines. The 4-and-5 weight
    # cues are sign-off pleasantries - "unfortunately we cannot reply to every
    # candidate", "we wish you the best in your job search" - and an ordinary
    # acknowledgement carries both, which sums to 9 and clears the threshold
    # for a *confident* rejection without a word of decline in the mail. So
    # weak cues corroborate a decline; on their own they stay below the line.
    if heaviest(REJECT_RULES, rej_hits) < 8:
        rej = min(rej, 7)

    advance = advancement_kind(text)

    # Only when the mail carries no decision of its own. A real assessment invite
    # also says "the link will expire" and hands over an access code; there the
    # invitation is the point and the account plumbing is incidental.
    if is_personal_mail(cand.get("from_address")):
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "sent from personal webmail, not an employer or ATS",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    if is_job_board(cand.get("from_address")) and rej < 8 and nxt < 8:
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "job board advertising openings, not a reply to an application",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    if EVENT_REMINDER.search(cand.get("subject") or ""):
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "reminder for an already-scheduled event, not a new response",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    if ACCOUNT_ADMIN.search(text) and rej < 8 and nxt < 8:
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "account setup or verification mail, not an application response",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    if EMPLOYMENT_LIFECYCLE.search(text):
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "about existing employment, not an application",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    # Yields to a decisive score, like the job-board gate: a real employer whose
    # mail happens to mention a benefits programme still gets read. It only
    # settles the weak end, which is where this mail lives - "next step",
    # "applications", "jobs", and no decision about the reader anywhere in it.
    if BENEFITS_PROGRAMME.search(text) and rej < 8 and nxt < 8:
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "employment programme or state job service, not an employer reply",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    if not looks_job_related(cand, text):
        return {
            "status": UNCLEAR, "confidence": "high",
            "note": "no job-application context in the mail",
            "explicit_round": None, "reject_score": rej, "next_score": nxt,
            "reject_hits": rej_hits, "next_hits": nxt_hits,
        }

    status, confidence, note = None, "low", ""
    if rej >= 8 and nxt < 8:
        status, confidence = REJECT, "high"
    elif nxt >= 8 and rej < 8:
        status, confidence = advance, "high"
        if ack:
            # A receipt that merely describes the pipeline ("Next Steps:
            # successful candidates move on to a video interview") or hedges
            # ("if we think you're a good fit ... schedule a call") trips these
            # rules without offering anything. Too close to call on keywords —
            # send it to the agent to read rather than banking a Pass.
            confidence = "low"
            note = "receipt language alongside next-round wording"
    elif rej >= 8 and nxt >= 8:
        status = REJECT if rej > nxt else advance
        note = "both reject and next-round language present (%d vs %d)" % (rej, nxt)
    # A receipt outranks a weak reject score on purpose. The 4-and-5 weight
    # cues are sign-off pleasantries - "we wish you success in your job
    # search", "unfortunately we cannot reply to every candidate" - and they
    # sit at the bottom of perfectly ordinary acknowledgements. A real decline
    # carries a decisive phrase and is settled by the rej >= 8 branch above,
    # so nothing is lost here; promoting a weak score over the receipt just
    # files five confirmations as rejections.
    elif ack and rej < 8 and nxt < 8:
        status, confidence, note = ACK, "high", "receipt only, no decision"
    elif rej > nxt and rej >= 4:
        status, note = REJECT, "weak reject signal only"
    elif nxt > rej and nxt >= 4:
        status, note = advance, "weak next-round signal only"
    else:
        status, note = UNCLEAR, "no decisive language"

    return {
        "status": status,
        "confidence": confidence,
        "note": note,
        "explicit_round": explicit_round(text) if status == INTERVIEW else None,
        "reject_score": rej,
        "next_score": nxt,
        "reject_hits": rej_hits,
        "next_hits": nxt_hits,
    }


# Where a company name came from, best casing first. company_from_domain has to
# .title() a bare domain ("gitlab.com" -> "Gitlab"), so it loses the real casing;
# the agent and the sender/body paths preserve it ("GitLab"). One spelling per
# employer keeps the same company from appearing twice.
