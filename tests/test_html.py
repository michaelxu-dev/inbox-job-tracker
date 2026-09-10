"""The page has to say the same thing the CSV says, and say it safely.

Two risks worth pinning. One is meaning: the CSV is one row per stage and the
page is one row per application, so the collapsing must not lose or reorder a
history. The other is injection - every string on the page came out of somebody's
mailbox, and a subject line is an excellent place to hide a `</script>`.
"""
import json
import os
import re

from inboxjobtracker import html as html_mod


def stage(company, position, status, date, **kw):
    row = {"company": company, "position": position, "status": status,
           "date": date, "received": date + "T09:00:00Z",
           "subject": kw.get("subject", "s"), "from_address": kw.get("sender", "a@b.com"),
           "evidence": kw.get("evidence", ""), "web_link": kw.get("link", ""),
           "decided_by": kw.get("by", "rules")}
    return (row["received"], company, position, row)


def test_one_entry_per_application_not_per_stage():
    rows = [stage("GitLab", "Senior Backend Engineer", "Acknowledge", "2026-08-29"),
            stage("GitLab", "Senior Backend Engineer", "Invite to first interview", "2026-08-30"),
            stage("GitLab", "Senior Backend Engineer", "Reject", "2026-09-09")]
    apps = html_mod.group_applications(rows)
    assert len(apps) == 1
    assert [s["status"] for s in apps[0]["stages"]] == [
        "Acknowledge", "Invite to first interview", "Reject"]


def test_the_stage_reached_is_the_furthest_not_the_last():
    """A rejection arrives after the interview. Reading the last mail instead of
    the furthest stage would erase the fact that an interview ever happened."""
    rows = [stage("GitLab", "Eng", "Invite to first interview", "2026-08-30"),
            stage("GitLab", "Eng", "Reject", "2026-09-09")]
    app = html_mod.group_applications(rows)[0]
    assert app["reached"] == "Reject"
    rows = [stage("Acme", "Eng", "Acknowledge", "2026-08-01"),
            stage("Acme", "Eng", "Invite to first interview", "2026-08-30")]
    assert html_mod.group_applications(rows)[0]["reached"] == "Invite to first interview"


def test_a_receipt_and_nothing_else_is_still_waiting():
    """"Acknowledge" is what the mail said; "no reply yet" is what it means once
    it is the only thing that ever arrived."""
    rows = [stage("Acme", "Eng", "Acknowledge", "2026-08-01")]
    assert html_mod.group_applications(rows)[0]["reached"] == html_mod.WAITING


def test_summary_counts_a_rejection_as_a_reply():
    rows = [stage("A", "Eng", "Acknowledge", "2026-08-01"),
            stage("B", "Eng", "Acknowledge", "2026-08-01"),
            stage("B", "Eng", "Reject", "2026-08-09")]
    stats = dict(html_mod.summarise(html_mod.group_applications(rows)))
    assert stats["Applications"] == 2
    assert stats["No reply yet"] == 1
    assert stats["Reply rate"] == "50%"


def test_a_hostile_subject_cannot_reach_the_markup():
    """The data is embedded as JSON, so the escape that matters is the one that
    stops a string closing the script block early."""
    rows = [stage("Acme", "Eng", "Reject", "2026-08-01",
                  subject="</script><img src=x onerror=alert(1)>",
                  evidence="<b>not bold</b>")]
    page = html_mod.render(html_mod.group_applications(rows), "gmail", len(rows))
    assert "</script><img" not in page
    assert "<b>not bold</b>" not in page
    # and the payload still parses as the JSON the page will read
    blob = re.search(r"var DATA = (.*?);</script>", page, re.S).group(1)
    data = json.loads(blob.replace("<\\/", "</"))
    assert data["apps"][0]["stages"][0]["subject"].startswith("</script>")


def test_the_page_needs_no_network():
    """It holds someone's correspondence; reading it must not phone anywhere."""
    rows = [stage("Acme", "Eng", "Reject", "2026-08-01")]
    page = html_mod.render(html_mod.group_applications(rows), None, 1)
    assert not re.search(r'(?:src|href)\s*=\s*"(?!#)(?!mailto:)[a-z]+:', page)
    assert "cdn" not in page.lower()


def test_write_html_round_trips(tmp_path):
    store = {"1": {"company": "Acme", "position": "Eng", "status": "Reject",
                   "date": "2026-08-01", "received": "2026-08-01T09:00:00Z",
                   "subject": "s", "from_address": "a@acme.com", "evidence": "e",
                   "web_link": "", "decided_by": "rules"}}
    out = tmp_path / "applications.html"
    assert html_mod.write_html(store, str(out), None, "gmail") == 1
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert "Acme" in page and os.path.getsize(str(out)) > 1000
