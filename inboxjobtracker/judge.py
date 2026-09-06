"""Optional LLM second opinion, via the Anthropic API.

The rules are fast and free but literal: they have read "we are unable to move
forward with your application" as an advancement, and a layoff letter as a
rejection. This asks a model to read the body of anything they were unsure
about, plus a rotating sample of what they were *confident* about — confidence
is where the expensive mistakes have been.

Entirely optional. With no API key the pipeline still runs on rules alone.
"""
import json
import os
import sys

PROMPT = """You are classifying one email from a job seeker's mailbox.

Decide which stage of an application it represents, if any.

- "Reject" - the employer has declined the application.
- "Acknowledge" - a receipt confirming the application arrived. A mail that only
  DESCRIBES what happens next ("successful candidates move on to a video
  interview", "if you are selected we'll be in touch") is an Acknowledge, not an
  invitation: nothing has been offered to this candidate.
- "Invite to test" - an assessment, coding test or take-home is to be completed,
  even if an interview is mentioned in passing.
- "Invite to interview" - a conversation with people is offered: screen, call,
  video interview, onsite. Use this generic form; round numbers are assigned
  separately from the order invitations arrived.
- "Unclear" - anything that is not an employer answering an application: job
  alerts, newsletters, recruiter cold pitches, account verification and sign-in
  codes, reminders for an already-booked interview, and mail about a job the
  person already holds (a termination letter reads exactly like a rejection).

Also give the employer's name - never the ATS vendor (greenhouse, workday, lever,
icims, ashby and friends). Dig it out of the subject or body when the sender is a
vendor. Use the ordinary trading name.

Reply with JSON only: {"status": "...", "company": "...", "position": "...",
"note": "one short line on why"}. Use null for company or position if the mail
genuinely does not say.

Subject: %(subject)s
From: %(from_name)s <%(from_address)s>

%(body)s
"""


def available(cfg):
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and cfg.get("judge") != "off"


def judge_one(client, cfg, item):
    message = client.messages.create(
        model=cfg.get("judge_model", "claude-haiku-4-5-20251001"),
        max_tokens=300,
        messages=[{"role": "user", "content": PROMPT % {
            "subject": item.get("subject", ""),
            "from_name": item.get("from_name", ""),
            "from_address": item.get("from_address", ""),
            "body": (item.get("body") or "")[:4000],
        }}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("model did not return JSON: %r" % text[:200])
    return json.loads(text[start:end + 1])


def judge(cfg, items):
    """Return {id: decision} for the items given. Failures are skipped, never
    fatal — a judged mailbox is better than no mailbox."""
    try:
        import anthropic
    except ImportError:
        sys.exit("The LLM judge needs the anthropic package:  pip install anthropic")

    client = anthropic.Anthropic()
    decisions = {}
    for i, item in enumerate(items, 1):
        try:
            verdict = judge_one(client, cfg, item)
        except Exception as exc:                      # noqa: BLE001 - report and continue
            print("  judge failed on %s: %s" % (item["id"][:12], exc), file=sys.stderr)
            continue
        verdict["id"] = item["id"]
        decisions[item["id"]] = verdict
        if i % 10 == 0:
            print("  judged %d/%d" % (i, len(items)), file=sys.stderr)
    return decisions
