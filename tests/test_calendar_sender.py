"""A calendar service writing from an employer's own domain.

Reported from a phone, on a Gmail mailbox: a Google application showed
"Thanks for applying to Google" and a Google online assessment filed under
**Xero**. One sender did it:

    calendar-notification@google.com
    Notification: Xero TX Screen Interview - Michelle @ Tue Oct 13, 2026 ...

The agent read Xero off that subject and was right. The domain map then
learnt that google.com means Xero, and every noreply@google.com mail -
Google's own recruiting - was renamed.

ATS_DOMAINS has carried calendar.google.com since the same fault was found
with a different sender. It could not catch this one: the domain here is
plain google.com, which really is Google's recruiting domain.

Mirrors Core/Tests/InboxJobTrackerCoreTests/CalendarSenderTests.swift.
"""
from inboxjobtracker.rules import company_from_domain, names_no_employer
from inboxjobtracker.report import canonical_by_domain, canonical_names, resolve_company


def row(id, address, company, source, subject=""):
    return {
        "id": id, "status": "Acknowledge", "date": "2026-10-%s" % id,
        "received": "2026-10-%sT10:00:00Z" % id, "subject": subject,
        "from_address": address, "company": company, "company_source": source,
        "position": "Software Engineer",
    }


STORE = {
    "13": row("13", "calendar-notification@google.com", "Xero", "agent",
              "Notification: Xero TX Screen Interview - Michelle "
              "@ Tue Oct 13, 2026 2:45pm - 3:30pm (PDT)"),
    "27": row("27", "noreply@google.com", "Google", "domain",
              "Thanks for applying to Google"),
    "18": row("18", "noreply@google.com", "Google", "domain",
              "[Due in 4 days] Next Step: Online Assessment stage for Google"),
}


def test_a_calendar_notice_does_not_rename_the_employers_own_mail():
    by_domain = canonical_by_domain(STORE)
    # google.com does map to Google - Google's own recruiting writes from it.
    # What must not happen is the notice teaching it Xero.
    assert by_domain.get("google.com") == "Google"

    canon = canonical_names(STORE)
    for id in ("27", "18"):
        assert resolve_company(STORE[id], canon, by_domain) == "Google"
    assert resolve_company(STORE["13"], canon, by_domain) == "Xero"


def test_the_domain_of_a_calendar_notice_names_nobody():
    assert company_from_domain("calendar-notification@google.com") is None
    assert company_from_domain("calendar-notification+abc@google.com") is None
    assert company_from_domain("CALENDAR-NOTIFICATION@google.com") is None


def test_google_is_still_an_employer():
    assert company_from_domain("noreply@google.com") == "Google"


def test_an_ordinary_notification_sender_is_untouched():
    """SmartRecruiters and BambooHR both write from one, and they are handled
    by their domains."""
    assert not names_no_employer("notification@contoso.com", "contoso.com")
    assert not names_no_employer("calendar@contoso.com", "contoso.com")


def test_the_shared_calendar_host_is_still_blocked():
    assert names_no_employer("c_263b7f96@group.calendar.google.com",
                             "group.calendar.google.com")
