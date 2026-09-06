# Contributing

## The most valuable contribution: an email we get wrong

This project is a pile of hard-won special cases. Every rule exists because a real
email was misread. So a misclassified email *is* the bug report.

1. Anonymise it — invent the company, the role, and your name. Keep the wording of
   the sentences that carry the decision; those are the whole point.
2. Add it to `tests/fixtures/emails.json`:

```json
{
  "id": "d13", "folder": "inbox",
  "from_name": "Example Careers", "from_address": "careers@example.test",
  "received": "2026-01-22T09:00:00Z",
  "subject": "An update on your application",
  "expect": "Reject",
  "why": "'we have decided to pursue other candidates' must outrank the invitation wording",
  "body": "..."
}
```

3. Run `pytest`. A red test with a clear `why` is a complete, mergeable bug report even
   if you don't fix it — open the PR anyway.

## Running the tests

```bash
pip install -e ".[dev]"
pytest -q
inbox-job-tracker demo      # end-to-end, no credentials needed
```

## Adding a mail source

Add `inboxjobtracker/sources/<name>.py` with a `fetch(cfg, days)` returning
`(candidates, scanned)`. Each candidate is a dict with `id`, `subject`, `from_name`,
`from_address`, `received` (ISO 8601 UTC), `body`, and optionally `folder` and
`web_link`. Register it in `sources/__init__.py`. Everything downstream is
source-agnostic, so that is genuinely all it takes.

## Style

- The classifier is the heart of this project. When you add a rule, say in a comment
  **which email made it necessary** — future readers cannot infer it, and will delete
  a rule that looks arbitrary.
- Prefer a narrow pattern that misses some mail over a broad one that misreads it. A
  missed rejection is annoying; a rejection reported as an interview invitation is worse.
- No new required dependencies in the core. `msal`/`requests` are optional extras for
  the Graph source, `anthropic` for the LLM judge; the rules and IMAP run on the
  standard library alone.
