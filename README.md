# inbox-job-tracker

[![tests](https://github.com/michaelxu-dev/inbox-job-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/michaelxu-dev/inbox-job-tracker/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill%20%2B%20subagent-D97757.svg)](#run-it-in-claude-code-recommended)

**Let an AI agent read your job-application replies and fill in the spreadsheet.**

Applied to sixty roles and lost track? Point it at your mailbox. It reads the replies
already sitting there and writes one row per application stage — who, what role, when,
and the sentence they actually said it in.

It ships as a **[Claude Code](https://claude.com/claude-code) skill and subagent**: one
slash command, no API key, and an agent that reads the mail rather than grepping it.

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

## Storing the row is easy. Reading the email is not

Every tracker can hold a spreadsheet. The work is deciding what a message *means* —
and a rejection and an invitation are written in nearly the same words. Each line
below is a real email that a keyword search reads backwards:

| The email says | Read literally | What it actually is |
|---|---|---|
| "we are **unable to move forward** with your application" | invitation — it says *move forward* | **rejection** |
| "If your application is a good fit, one of our team members will contact you to **schedule a call**" | first interview | **receipt** — nothing was offered |
| "if you see the job moved to an inactive state, that means … **you were not selected**" | rejection | **receipt** — that is a legend for a dashboard |
| "successful **candidates** move on to a video interview" | invitation | a description of their process |
| "I need a few items from you **before I can move forward**" | invitation | an agency asking for references |
| "**Reminder:** your upcoming interview" | a new interview | the one you already booked |

So the classifier reads context, not keywords: an advancement phrase preceded by a
negation, a hypothetical, a precondition or a third-person subject does not count.
Each row above is pinned by a fixture in `tests/` — every one of them shipped wrong
once.

And that is only the half a rule can be taught. The rest is judgement, and that is
what the agent is for: it reads the bodies the rules could not settle, decides what
each one means, and reports which rules it had to overrule — plus a rotating audit
of what they were *confident* about, because a confidently mislabelled rejection
never asks anyone. ([How it works](#how-it-works) has the full picture; you can also
run it with no AI at all, and the rules alone still produce the spreadsheet.)

Try it in ten seconds, no mailbox required:

```bash
git clone https://github.com/michaelxu-dev/inbox-job-tracker && cd inbox-job-tracker
python -m inboxjobtracker.cli demo
```

No install, no virtualenv, nothing to download — the core has zero dependencies
and runs on any Python 3.9+.

---

## Quick start

Five minutes to your first spreadsheet, using Gmail in
[Claude Code](https://claude.com/claude-code) — the way this is meant to be run, and
the one that adds the agent's judgement. Outlook is
[one section further down](#outlookcom--hotmail--microsoft-365), and everything here
[works from a plain terminal](#prefer-a-terminal) too.

### 1. Clone it

```bash
git clone https://github.com/michaelxu-dev/inbox-job-tracker
cd inbox-job-tracker
```

Stay in this terminal for steps 2 and 3 — Claude Code starts in step 4, and it
inherits the environment of the shell that launches it. Nothing to install and no
API key: the core has no dependencies and runs on any Python 3.9+, and the skill
and subagent ship in the repo's `.claude/` folder.

### 2. Point it at your mailbox

```bash
cp config.example.json config.json
```

Edit two things in `config.json`: your address in `own_addresses`, and
`imap_user` in the `gmail` account. Everything else has a working default.

### 3. Create an app password

Your normal password will not work. Turn on 2-Step Verification, then create one
at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
and put it in the environment — never in the config file:

```bash
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"   # PowerShell: $env:GMAIL_APP_PASSWORD="..."
```

A process only sees the variables that existed when it started, which is why this
comes before step 4. On Windows the same rule bites twice: a variable set in the
System Properties dialog does not reach a terminal that is already open. To make it
permanent rather than per-session, use `setx GMAIL_APP_PASSWORD "..."` and then open
a fresh terminal.

### 4. Open it in Claude Code and run it

From that same terminal:

```bash
claude
```

Then type:

```
/inbox-job-tracker 60 gmail
```

Two arguments, both optional and in either order: how many days, and which account
from your `config.json`. Plain words work as well — "check my job replies from the
last month" reaches the same run.

The skill fetches and classifies, hands the mail the rules could not settle to the
subagent to read, and merges the result. It will ask you about anything genuinely
ambiguous, and it reports the rows where the agent overruled the rules —
[why that matters](#run-it-in-claude-code-recommended).

If it stops saying the password variable is missing, Claude Code was started before
step 3. Exit it, check the variable is set (`echo $GMAIL_APP_PASSWORD`, or
`echo $env:GMAIL_APP_PASSWORD` in PowerShell), and start it again.

### 5. Read the result

`data/gmail/applications.csv`, one row per employer + role + stage, with the
sentence each verdict rests on and a link back to the message. Open it in Excel,
Numbers or Sheets.

Nothing is uploaded, nothing is marked as read, and nothing in your mailbox is
changed — the connection is read-only.

### Prefer a terminal?

Every step works without Claude Code. Install the command once:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e .
```

then run steps 2 and 3 as above and finish with:

```bash
inbox-job-tracker run --account gmail --days 60
```

That gives you the rules tier — free, offline, and the bulk of the mail. The
judgement on what it cannot call then needs
[an API key](#no-claude-code-the-same-judgement-over-the-api).
`pip install -e ".[graph]"` adds Outlook support, `".[dev]"` adds pytest — see
[Options](#options).

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

The [Quick start](#quick-start) above is this path. Every form of the command:

```
/inbox-job-tracker              # default account, the window from config.json
/inbox-job-tracker 30           # the last 30 days
/inbox-job-tracker 30 gmail     # ...of the gmail mailbox
/inbox-job-tracker gmail        # gmail, configured window
```

Plain words work as well — "the last month, use my gmail account" reaches the same
run. No API key: the skill and the subagent are in the repo, so they appear the
moment you open the folder.

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
the result. Everything below still works from an ordinary terminal if you would
rather — [see above](#prefer-a-terminal).

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

Both Gmail and Yahoo need an **App Password**, not your normal one:

| | Where to get it | Host |
|---|---|---|
| Gmail | 2-Step Verification on, then [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) | `imap.gmail.com` |
| Yahoo | [login.yahoo.com/account/security](https://login.yahoo.com/account/security) → Generate app password | `imap.mail.yahoo.com` |

`password_env` names the environment variable each account reads, so several mailboxes
can be open at once. Access is read-only — the tool can't send, move or delete mail, and
messages are fetched with `BODY.PEEK`, so nothing is marked as read behind you.

A folder in `imap_folders` that the provider doesn't have (Gmail labels and Yahoo's
`Bulk Mail` differ) is reported and skipped, not treated as an error.

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
  "default_account": "gmail",
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

Want the command on your `PATH` without managing a virtualenv?
[pipx](https://pipx.pypa.io) does it in one step: `pipx install .`

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

The diagram is the design. The rules are free, instant and offline, so they take the mail
whose meaning is unambiguous; everything else — plus that rotating audit sample — goes to
the agent. Each message is judged once and the verdict is cached in `store.json`, so
re-runs cost nothing.

The prefilter is deliberately generous: it's cheap to discard a non-HR email later and
expensive to never see a rejection at all. `store.json` is the durable record, so a
narrower `--days` window scans less mail without discarding anything already learned.

Which is affordable because the fetch itself is two passes, on both sources. The cheap
one reads headers and a slice of the body for everything in the window — 100 messages per
round trip over IMAP, one page of metadata over Graph. Only what survives the prefilter is
downloaded in full: on a real mailbox, 78 messages out of 1047.

## File structure

```
inbox-job-tracker/
├── config.example.json          # copy to config.json - mailboxes, window, thresholds
├── pyproject.toml               # packaging, the inbox-job-tracker command, optional extras
│
├── inboxjobtracker/             # the package
│   ├── cli.py                   # run | fetch | classify | judge | merge | demo | accounts
│   ├── config.py                # config + accounts resolution; each account owns data/<name>
│   ├── prefilter.py             # is this mail job-related at all? deliberately generous
│   ├── rules.py                 # the classifier: reject vs receipt vs invitation, and why
│   ├── report.py                # store -> applications.csv, one row per stage
│   ├── store.py                 # the durable record; makes re-runs incremental
│   ├── judge.py                 # optional LLM second opinion over the API
│   ├── graph_auth.py            # Microsoft OAuth device-code flow (MSAL)
│   └── sources/                 # interchangeable mailboxes: same fetch(cfg, days) contract
│       ├── imap.py              # Gmail, Yahoo, Fastmail - app password, two-pass fetch
│       ├── graph.py             # Outlook.com / Microsoft 365
│       └── demo.py              # synthetic mailbox, no credentials needed
│
├── .claude/                     # ships with the repo; appears when you open it in Claude Code
│   ├── skills/inbox-job-tracker/SKILL.md    # the /inbox-job-tracker command
│   └── agents/inbox-job-tracker.md          # the subagent that reads the queued mail
│
├── tests/
│   ├── fixtures/emails.json     # every email the classifier once got wrong
│   ├── test_rules.py            # ...and what it must say about each one
│   └── test_config.py           # accounts, per-account stores, IMAP fetch and links
│
├── docs/outlook-setup.md        # the Azure app registration, five minutes
└── data/                        # created on first run, gitignored - your mail lives here
    └── <account>/               # candidates.json, store.json, review_queue.json,
                                 # decisions.json, applications.csv
```

Two files are worth knowing by name. `data/<account>/store.json` is the durable
record — everything else in `data/` is regenerated from it, which is why the CSV
should never be hand-edited. And `tests/fixtures/emails.json` is the project's
memory: each entry is a real misclassification, with what it should say and why,
so the same bug cannot come back.

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
