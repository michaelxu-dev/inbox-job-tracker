"""Shared prefilter and fetch-window helper, used by every mail source.

Deliberately generous: a false positive costs one cheap classification, a false
negative loses a rejection forever. Source-agnostic so the Graph reader and the
Outlook COM reader cannot drift apart.
"""
import re
import sys

SUBJECT_HINTS = re.compile(
    r"\b(application|applying|applied|candidat\w+|interview|recruit\w*|talent|"
    r"hiring|position|vacancy|job|req(uisition)?|role at|your submission|"
    r"next steps?|assessment|screening|offer)\b",
    re.I,
)
BODY_HINTS = re.compile(
    r"(thank you for (your interest|applying)|your application|applied (for|to)|"
    r"the (position|role) of|we regret|unfortunately|move forward|not selected|"
    r"schedule (a|an) (call|interview)|hiring team|talent acquisition|"
    r"recruit(er|ing) team|other candidates)",
    re.I,
)
ADDRESS_HINTS = re.compile(r"(careers?|recruit\w*|talent|hr|jobs?|hiring|no-?reply)", re.I)
NAME_HINTS = re.compile(
    r"(careers?|recruit\w*|talent acquisition|hiring|human resources)", re.I
)

# Mail from these platforms is almost always application-related.
ATS_DOMAINS = (
    "myworkday.com", "workday.com", "greenhouse.io", "lever.co", "hire.lever.co",
    "icims.com", "taleo.net", "oraclecloud.com", "smartrecruiters.com",
    "ashbyhq.com", "successfactors.com", "sap.com", "jobvite.com", "workable.com",
    "breezy.hr", "bamboohr.com", "recruitee.com", "teamtailor.com", "avature.net",
    "brassring.com", "silkroad.com", "jazzhr.com", "applytojob.com", "eightfold.ai",
    "phenompeople.com", "hiringthing.com", "paylocity.com", "dayforcehcm.com",
    "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com",
)


def is_candidate(subject, from_name, from_address, preview):
    """Return the list of reasons this message looks application-related ([] if not)."""
    subject = subject or ""
    from_name = from_name or ""
    from_address = (from_address or "").lower()
    preview = preview or ""

    domain = from_address.split("@")[-1] if "@" in from_address else ""
    reasons = []
    if domain and any(domain == d or domain.endswith("." + d) for d in ATS_DOMAINS):
        reasons.append("ats-domain")
    if SUBJECT_HINTS.search(subject):
        reasons.append("subject")
    if BODY_HINTS.search(preview):
        reasons.append("body-preview")
    if ADDRESS_HINTS.search(from_address):
        reasons.append("sender-address")
    if NAME_HINTS.search(from_name):
        reasons.append("sender-name")
    return reasons


def lookback_days(cfg, argv=None):
    """Days of mail to scan. `--days N` (or a bare N) overrides lookback_days in
    config.json for one run, so a quick "just the last week" check needs no edit
    to the config. The store is durable either way — a short window scans less,
    it does not discard what earlier runs already recorded."""
    argv = list(sys.argv[1:] if argv is None else argv)
    value = None
    for i, arg in enumerate(argv):
        if arg in ("--days", "-d") and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith("--days="):
            value = arg.split("=", 1)[1]
        elif arg.isdigit():
            value = arg
    if value is None:
        return int(cfg["lookback_days"])
    try:
        days = int(value)
    except ValueError:
        sys.exit("--days needs a whole number of days, got %r" % value)
    if days < 1:
        sys.exit("--days must be at least 1, got %d" % days)
    return days
