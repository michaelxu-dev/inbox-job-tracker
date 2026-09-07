# inbox-job-tracker

[![tests](https://github.com/michaelxu-dev/inbox-job-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/michaelxu-dev/inbox-job-tracker/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill%20%2B%20subagent-D97757.svg)](#run-it-in-claude-code-recommended)

**Your mailbox already knows how your job search is going. An AI agent turns it into a
spreadsheet.**

Applied to sixty roles and lost track? `inbox-job-tracker` reads the replies sitting in your
inbox and writes one row per application stage — who, what role, when, and what they
actually said.

It ships as a **[Claude Code](https://claude.com/claude-code) skill and subagent**, so the
whole thing runs from one slash command and an AI reads the mail that needs judgement.
The design is deliberately two-tier: deterministic rules settle the mail that is obvious,
free and offline, and a model is spent only on what they cannot call — plus a rotating
audit of what they were *confident* about, which is where classifiers are wrong in the
ways that cost you. You can also run it with no AI at all; the rules alone still produce
the spreadsheet.

```
CompanyName  Position                 Status                     Sender                  Notes
Northwind    Senior Backend Engineer  Acknowledge                no-reply@greenhouse...  Thanks for applying to Northwind Robotics
Northwind    Senior Backend Engineer  Reject                     no-reply@greenhouse...  Unfortunately, at this time we are unable to move forward
Contoso      Staff Platform Engineer  Acknowledge                contoso@myworkday.com   Thank you for your interest in a career at Contoso
Contoso      Staff Platform Engineer  Invite to first interview  dana.reed@contoso.com   We would like to invite you to the next stage
Fabrikam     Senior Data Engineer     Acknowledge                careers@fabrikam...     Thanks for applying to Fabrikam
Fabrikam     Senior Data Engineer     Invite to test             no-reply@greenhouse...  You have been invited to complete an online assessment
```

*(That is real output — it is exactly what `demo` below prints. Dates and the
Web Link column are trimmed here for width.)*

Try it in ten seconds, no mailbox required:

```bash
git clone https://github.com/michaelxu-dev/inbox-job-tracker && cd inbox-job-tracker
python -m inboxjobtracker.cli demo
```

No install, no virtualenv, nothing to download — the core has zero dependencies
and runs on any Python 3.9+.

<details>
<summary>Prefer a real command instead of <code>python -m</code>?</summary>

**To just use it** — [pipx](https://pipx.pypa.io) keeps it isolated and puts the
command on your `PATH` in one step:

```bash
pipx install .          # then: inbox-job-tracker demo
```

**To work on it** — a virtualenv, so `pytest` and an editable install stay out of
your system Python:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
```

`pip install --user` also works, but on Windows it drops the launcher in
`%APPDATA%\Python\Python3xx\Scripts`, which is usually **not** on `PATH` — so
`inbox-job-tracker: command not found` is the common outcome. If you hit that
from any install method, `python -m inboxjobtracker.cli` always works from the
repo root.

</details>

---

## Why this is harder than it looks

A rejection and an invitation are written in nearly the same words. Every rule below
exists because a naive version got a real email wrong:

| The email says | Naive reading | Actually |
|---|---|---|
| "we are **unable to move forward** with your application" | invitation — it says *move forward* | **rejection** |
| "**if we decide to move forward**, we'll be in touch" | invitation | receipt; nothing offered |
| "successful **candidates** move on to a video interview" | invitation | a description of their process |
| "**Thank you for your interest**" + a verification link | receipt | account setup |
| "**Reminder:** your upcoming interview" | a new interview | the one you already booked |

So the classifier reads context, not keywords: an advancement phrase preceded by a
negation, a hypothetical, or a third-person subject doesn't count. `tests/` pins down
every one of these — each fixture is a bug that shipped once.

## What you get

- **One row per employer + role + stage.** An application reaches each stage once, so a
  resent rejection or an assessment announced and then issued collapse into one row.
- **Interview rounds counted properly.** Arranging one interview takes a request for
  availability, a calendar invite, maybe a reschedule. Those are one round, not three.
- **The sentence the decision rests on**, quoted in a Notes column, so you never have to
  trust it blindly — plus a link straight back to the original message.
- **Nothing thrown away.** Job alerts, newsletters and recruiter spam are classified
  `Unclear` and kept out of the spreadsheet, not deleted.

## Run it in Claude Code (recommended)

Clone the repo, open it in [Claude Code](https://claude.com/claude-code), and type:

```
/inbox-job-tracker 30                          # the last 30 days
/inbox-job-tracker 30 use my gmail account     # name a mailbox in plain words
```

No API key. The skill and the subagent are in the repo, so they appear the moment you
open the folder.

**Why this is the better way to run it**

- **The judgement is done by an agent, not a keyword.** The subagent reads the message
  bodies the rules could not settle and decides what each one actually means — that a
  mail describing a hiring funnel is a receipt, not an interview invitation.
- **Your correspondence stays out of the conversation.** The subagent
  (`.claude/agents/`) reads the mail in its own context and returns only a summary, so
  message bodies never enter the main transcript.
- **It tells you which rules were wrong.** Every run reports the rows where the agent
  overruled the rules — that list is how the classifier gets better instead of quietly
  repeating a mistake for months.
- **It asks you when a mail is genuinely ambiguous** instead of guessing. You know which
  company you applied to; an unattended run does not.

The skill (`.claude/skills/`) is the entry point: it takes the time range and the mailbox
as plain arguments, runs fetch and classify, hands the reading to the subagent, and merges
the result. Everything below still works from an ordinary terminal if you would rather.

## Setup

> The commands below are written as `inbox-job-tracker`, which exists once you
> install it. If you are running straight from the clone, use
> `python -m inboxjobtracker.cli` instead — every command works the same either way.

### Gmail, Fastmail, or any IMAP provider — no app registration

```bash
cp config.example.json config.json     # set imap_user and own_addresses
export GMAIL_APP_PASSWORD="your-app-password"
inbox-job-tracker run --account gmail
```

Gmail needs an **App Password**, not your normal one: turn on 2-Step Verification, then
create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
Access is read-only — the tool can't send, move or delete mail.

### Outlook.com / Hotmail / Microsoft 365

Set `"source": "graph"` and follow [docs/outlook-setup.md](docs/outlook-setup.md). It's a
free Azure app registration, about five minutes, and the guide covers the three settings
that fail confusingly if you miss them.

### More than one mailbox

Applying from a personal address and a work one is normal, so `config.json` holds as many
mailboxes as you like. Settings at the top level are shared; each block under `accounts`
overrides only what differs:

```json
{
  "own_addresses": ["you@gmail.com", "you@outlook.com"],
  "default_account": "outlook",
  "accounts": {
    "outlook": {"source": "graph", "client_id": "..."},
    "gmail":   {"source": "imap", "imap_user": "you@gmail.com",
                "password_env": "GMAIL_APP_PASSWORD"}
  }
}
```

```bash
inbox-job-tracker accounts                  # what this config defines
inbox-job-tracker run --account gmail       # or JOBTRACKER_ACCOUNT=gmail
inbox-job-tracker run                       # default_account, else the first defined
```

Each account keeps **its own store** in `data/<account name>` — and its own Graph token
cache in `token_cache-<account name>.json` — so one mailbox's history never merges into
another's, and each spreadsheet
covers one inbox. `password_env` names the variable holding that account's password, so
two IMAP mailboxes can be open at once. A config with no `accounts` block still describes
a single mailbox the flat way, exactly as before.

### Options

```bash
inbox-job-tracker run --days 7        # just this week; the spreadsheet keeps its full history
inbox-job-tracker fetch               # read mail
inbox-job-tracker classify            # apply the rules
inbox-job-tracker judge               # optional: ask an LLM about the uncertain ones
inbox-job-tracker accounts            # the mailboxes this config defines
```

`--account NAME` picks a mailbox and `--days N` a time range; both are accepted on
either side of the subcommand. Pass the same `--account` to every command in a run,
or `merge` writes into a different store than `classify` filled.

## No Claude Code? The same judgement over the API

The rules are fast, free and literal. They handle most mail correctly, and everything
they're unsure about lands in that account's `review_queue.json` rather than
being guessed at.

Outside Claude Code, an API key gets those read — the same two-tier design, the same
prompt, just billed per message instead of running in your session:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # then set "judge": "on" in config.json
inbox-job-tracker judge
```

It reads two kinds of mail: what the rules found **uncertain**, and a rotating sample of
what they were **confident** about. That second part matters — in practice, confidence is
where the expensive mistakes are. A confidently mislabelled rejection never asks anyone.

Cost is a fraction of a cent per email on Haiku, and each mail is judged once and cached,
so a re-run costs nothing.

## How it works

```
mailbox ──► prefilter ──► rules ──┬──► confident verdict ───────────┐
                                  │                                 ├──► applications.csv
                                  └──► review queue ──► AI agent ───┘
                                      (Claude Code, or the API)

every verdict is cached in store.json — durable, so re-runs are incremental
```

Two tiers, and the split is the whole idea. The rules are free, instant and offline, so
they take the mail whose meaning is unambiguous. Everything else — plus a rotating sample
of what the rules were *confident* about — goes to the agent, because a confidently
mislabelled rejection never asks anyone. Each message is judged once and the verdict is
cached in `store.json`, so re-runs cost nothing.

The prefilter is deliberately generous: it's cheap to discard a non-HR email later and
expensive to never see a rejection at all. `store.json` is the durable record, so a
narrower `--days` window scans less mail without discarding anything already learned.

## Privacy

Your mail is read locally and stays on your machine. The rules run entirely offline.
Nothing leaves the machine unless you use the AI tier, and then only the messages queued
for review — never the whole mailbox. In Claude Code the subagent reads them in an
isolated context, so they stay out of the main conversation. `config.json`,
`token_cache*.json` and `data/` are all gitignored. Passwords and API keys are never read
from the config file — only from the environment, which is why each account names its own
variable in `password_env`.

## Contributing

The most useful contribution is **an email the classifier gets wrong**. Anonymise it, add
it to `tests/fixtures/emails.json` with what it should be and why, and open a PR — a
failing fixture is a complete bug report. See [CONTRIBUTING.md](CONTRIBUTING.md).

Wanted: more mail sources (Yahoo, Proton Bridge), non-English rejection wording, and ATS
vendors that hide the employer's name.

## Licence

MIT.
