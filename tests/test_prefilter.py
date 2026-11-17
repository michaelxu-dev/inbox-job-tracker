"""What the prefilter lets through, and what it must keep out.

Written when a real interview confirmation was found missing from a store of
499 messages. Nothing reported it: twelve other mails from the same employer
were kept, the thread looked complete, and the reply to the lost mail survived
on its own quoted text. It surfaced only because the interview it booked had
no time on screen.

The prefilter is the one gate with no second chance. Everything after it can
be corrected by hand; a message dropped here is never seen again, and is not
counted anywhere that would show it was dropped.

Mirrors Core/Tests/InboxJobTrackerCoreTests/PrefilterTests.swift.
"""
from inboxjobtracker.prefilter import is_candidate


ASANA_PREVIEW = (
    "Hi Michael,\r\n\r\n"
    "Thank you for sharing your availability! Please reply-all to this email "
    "with 'confirmed' if you received this confirmation:\r\n\r\n"
    "Date/Time: Nov 9, 2026 8:00pm-8:30pm UTC\r\n"
    "Interviewers: Heta Patel\r\n"
)


def test_a_coordinator_confirming_a_booked_interview():
    """The mail that prompted this, verbatim from the mailbox it was lost in."""
    assert is_candidate(
        "Asana Confirmation: (Michael Xu), Senior Security Engineer, Threat Response",
        "Heta Patel", "hetapatel@asana.com", ASANA_PREVIEW,
    ) == ["body-preview"]


def test_either_phrase_is_enough_on_its_own():
    """Either half would have saved it, so each is asserted separately."""
    for preview in ("Interviewers: Dana Lee",
                    "Interviewer: Dana Lee",
                    "Thanks for sending your availability."):
        assert is_candidate("Re: next week", "", "someone@example.com", preview)


def test_the_confirmations_that_are_not_about_a_job():
    """Measured on 3000 real messages: ``confirm\\w*`` in the subject would have
    caught the Asana mail and twelve of these with it. They are the reason the
    fix went in the body pattern instead, and they must stay out."""
    for subject in ("Basic BCeID Account Confirmation",
                    "Confirmation of Registration - Interac verification service",
                    "Confirm Your Name Before the Letter Goes Out",
                    "SpaceX IPO Confirmed: Claim Your Stake Today",
                    "Middle East Headlines, Fed Chair Confirmation Hearing",
                    "EI Confirmation"):
        assert "subject" not in is_candidate(subject, "", "noreply@example.com", "")


def test_stock_availability_is_not_an_interview():
    """"Availability" alone is a shop's word as often as a recruiter's. The
    pattern asks for *your* availability - somebody writing to a person about
    their time."""
    assert is_candidate("Back in stock", "", "news@shop.example",
                        "Check availability in your area before it sells out.") == []


def test_the_old_entrances_still_work():
    assert "subject" in is_candidate(
        "Your application to Contoso", "", "x@example.com", "")
    assert "ats-domain" in is_candidate(
        "Hello", "", "no-reply@greenhouse-mail.io", "")
