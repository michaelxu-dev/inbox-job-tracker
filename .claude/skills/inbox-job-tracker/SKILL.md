---
name: inbox-job-tracker
description: Scan the mailbox for replies to job applications and update data/applications.csv. Use when asked to check job application responses, refresh the tracker, or find rejections and interview invites in email. Takes two optional arguments: the number of days to scan and the mailbox account, e.g. "60 gmail".
---

# Check job application replies

Reads the user's mailbox, works out what each reply means for an application, and
writes `data/applications.csv`.

## Arguments

Two, both optional, in either order: **a number of days** and **an account name**.

```
/inbox-job-tracker                 # default_account, lookback_days from config (90)
/inbox-job-tracker 60              # last 60 days, default account
/inbox-job-tracker 60 gmail        # last 60 days of the gmail mailbox
/inbox-job-tracker gmail           # gmail, configured lookback
```

- **The number** becomes `--days N`. Words work too: "this week" → 7, "this month"
  → 30, "since yesterday" → 1. A short window only scans less mail; it never
  shrinks the spreadsheet, because the store is durable and history from earlier
  runs stays.
- **The word** is an account name from `config.json` and becomes `--account NAME`,
  passed to **every** command in the run — `merge` included, or it writes to a
  different store than `classify` filled. Each account keeps its own store in
  `data/<account>/`, so histories never merge. Without one, the config's
  `default_account` applies.

Run `inbox-job-tracker accounts` to see the names. If the word given is not one of
them, do not guess and do not fall back to the default: say which accounts exist
and stop. Running the wrong mailbox writes a store the user did not ask for.

Phrasings like "use my gmail account" or "the other mailbox" mean the same thing —
take the account name out of the sentence.

Anything else typed after the command is an instruction, not an argument. One
arrives often:

- **"just re-judge" / "don't re-fetch"** — skip step 1 and go straight to step 2.
  The queue from the last run is still in the account's `review_queue.json`.

`--config <path>` selects a different config file altogether, which is a rarer
need than picking an account inside the current one.

For anything else — turning the LLM judge on, changing how many mails are reviewed
per run — say which setting in `config.json` controls it and let the user decide.
Do not edit their config unasked.

## Steps

**1. Fetch and classify.** These are mechanical and cheap:

```
inbox-job-tracker fetch --days N      # omit --days for the configured default
inbox-job-tracker classify
```

Add `--account <name>` to both when the user named a mailbox. **Every path below
is inside that account's data directory** — `data/<account>/`, so
`data/outlook/review_queue.json` for the `outlook` account. The bare `data/...`
paths are written for a single-mailbox config.

If `inbox-job-tracker` is not on PATH, use `python -m inboxjobtracker.cli` instead.
If fetch reports missing credentials, relay its message — it names the exact
environment variable — and stop. Setup needs the user's browser or password
manager; it cannot be done for them.

Graph fetches over a wide window take several minutes. Let them finish.

**2. Judge what the rules could not call.** `classify` prints how many items it
queued. If that number is zero, skip to step 3.

The rules are literal, and their mistakes are systematic: they have read "we are
unable to move forward with your application" as an invitation, and a description
of a hiring process as an offer. `data/review_queue.json` holds what they were
unsure about, plus a rotating sample of what they were *confident* about — the
`reason` field says which, and both need reading.

Pick the path that is available:

- **Preferred — delegate to the subagent.** Call the Agent tool with
  `subagent_type: "inbox-job-tracker"` (defined in `.claude/agents/`). It reads
  the message bodies in its own context, so personal correspondence never enters
  this conversation, and returns a summary. Its own instructions cover how to
  judge; the prompt only needs to say:

  - do the **judgement pass only** — read `data/review_queue.json`, write
    `data/decisions.json`, then run `inbox-job-tracker merge`. Name the account's
    directory explicitly when one is in use, and tell the agent to pass the same
    `--account` to `merge`;
  - do **not** re-run fetch or classify, both are already done;
  - report the final row count by status, and separately every item where it
    corrected `rule_status` — that list is what says which rules still need work.

  If no agent by that name is defined, or the Agent tool is unavailable, or the
  call fails, fall through to the next option rather than reading the queue here.
  Nothing is lost by doing so: `classify` has already written the CSV, and the
  queue stays on disk for a later run.
- **Otherwise, if `ANTHROPIC_API_KEY` is set:** `inbox-job-tracker judge`.
- **Otherwise:** say plainly that the rules alone produced the CSV, that
  `data/review_queue.json` holds the uncertain items, and that setting
  `ANTHROPIC_API_KEY` or using the subagent would get them read. Do not read
  dozens of message bodies into this conversation to compensate.

**3. Report.** Give the user:

- the row count, split by status;
- any item where the judgement corrected the rules, one line each — this is the
  most useful part, because it says which rules still need work;
- anything genuinely ambiguous. **Ask the user** rather than guessing: they know
  which company they applied to, and this is the advantage a skill has over an
  unattended run. One real case was an ATS mail naming no employer, where the
  answer was recoverable only from the sender's subdomain.

## When the user disputes a row

Take it as one instance of a class, not a one-off. Trace why the classifier read
it that way — `data/store.json` keeps each row's scores, matched sentence and
`decided_by` — then check the whole CSV for the same shape and say how many you
found. Add the email to `tests/fixtures/emails.json` with what it should be and
why, so the fix is pinned by a test.

Two invariants the CSV must satisfy, both enforced in code; if either breaks,
that is a bug worth fixing rather than editing the CSV:

- one row per employer + role + status;
- stages never run backwards (acknowledge, then test or interview, then reject).

## Never

- Hand-edit `data/applications.csv`. It is regenerated from `data/store.json`
  every run; change the store or the rules.
- Delete `data/store.json` to start clean unless the user asks. It is the durable
  record and what makes re-runs incremental.
- Print or copy `token_cache.json`, or any password or API key.
- Quote message bodies at length. They are personal correspondence; use them to
  classify and refer to them by company and date.
