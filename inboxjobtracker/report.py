"""Turning the classified store into applications.csv.

One row per employer + role + status: an application reaches each stage once, so
a resent rejection or an assessment announced and then issued collapse to the
earliest mail, which is when that stage was actually reached.
"""
import collections
import csv
import datetime as dt
import json
import re
import sys

from .rules import (ACK, ATS_DOMAINS, backfill_positions, fold_position_keys, name_key,
                    unknown_labels,
                    position_key, position_words, CSV_STATUSES, INTERVIEW, INTERVIEW_ROUNDS,
                    REJECT, STAGE_ORDER, SUBDOMAIN_NOISE)


# "user" is a correction someone typed. Nothing derived outranks it, and the
# iOS app writes it into the same store.json this reads.
#
# "ai" is that app's spelling of "agent", and both appear in a store the two
# tools have shared. Missing it cost the lookup its default of 4 - worse than
# a name read off the sending domain - so on a real mailbox a Xero interview
# booked through karat.io was filed under "Karat", the vendor, in the Python
# and under "Xero" in Swift. The same mismatch runs the other way:
# `CompanySource` in Swift did not know "agent" and scored it `unknown`.
SOURCE_RANK = {"user": -1, "agent": 0, "ai": 0, "text": 1, "sender-name": 2,
               "domain": 3, "ats-localpart": 4, "unknown": 5}


def spelling_rank(row):
    """How much to trust this row's spelling of the employer's name.

    Source first. Then, between two names from equally good sources, the one
    that kept its own casing: company_from_domain has to .title() a bare domain,
    so "ea.com" becomes "Ea", while restore_casing recovers "EA" from a mail
    that writes it that way. Both are rank 3 and only the tie-break tells them
    apart - without it the winner is whichever row was read first.
    """
    name = row.get("company") or ""
    return (SOURCE_RANK.get(row.get("company_source"), 4),
            0 if name != name.title() else 1)


# Prefixes a mail carries while one interview is being arranged. Stripping them
# lets "Contoso: Interview Request!" and its "Re:" reply be recognised
# as one conversation rather than two invitations.
SCHEDULING_PREFIX = re.compile(
    r"^\s*((re|fw|fwd|invitation|updated invitation|accepted|declined|confirmed|"
    r"cancell?ed|rescheduled|reminder)\s*:\s*)+", re.I)


def thread_key(subject):
    text = SCHEDULING_PREFIX.sub("", subject or "")
    text = re.sub(r"\s*@.*$", "", text)      # calendar tail: "@ Mon Aug 17, 2026"
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def assign_interview_rounds(store, gap_days=10):
    """Turn the generic INTERVIEW marker into first/second/third.

    A round is an interview, not an email. Arranging one takes several mails —
    a request for availability, a calendar invitation, sometimes a reschedule —
    and counting those as separate rounds invents interviews that never
    happened. Mails belong to the round already under way when they share the
    day, continue its subject thread, or land within `gap_days` of its start;
    a genuine next round follows the interview itself, so it sits further out.
    Wording that states a round outright always wins and resets the count.
    """
    groups = {}
    for row in store.values():
        if row["status"] == INTERVIEW or row["status"] in INTERVIEW_ROUNDS:
            key = (name_key(row.get("company") or "?"),
                   position_key(row.get("position") or ""))
            groups.setdefault(key, []).append(row)

    for rows in groups.values():
        rows.sort(key=lambda r: r["received"])
        count, round_start, threads = 0, None, {}
        for row in rows:
            date = dt.date.fromisoformat(row["date"])
            stated = row.get("explicit_round")
            key = thread_key(row.get("subject"))
            if stated:
                # A stated round is trusted, but not past the end of the
                # history: you cannot be invited to a third interview having
                # been invited to none. A contract posting advertising "3 days
                # onsite" once made a single interview a third one, and the row
                # named two earlier rounds that never happened. Capping at the
                # next round keeps a real "final round" ordinal while making the
                # impossible one unrepresentable.
                count, round_start = min(stated, count + 1), date
            elif row.get("agent_status") in INTERVIEW_ROUNDS:
                # Only an ordinal the agent set deliberately is authoritative.
                # The one this function writes is stored too, and reading that
                # back would make a round number permanent once assigned.
                count = INTERVIEW_ROUNDS.index(row["agent_status"]) + 1
                round_start = date
            elif key and key in threads:
                count = threads[key]                       # same conversation
            elif round_start is not None and (date - round_start).days <= gap_days:
                pass                                       # still arranging this one
            else:
                count, round_start = count + 1, date
            count = max(count, 1)
            row["status"] = INTERVIEW_ROUNDS[min(count, len(INTERVIEW_ROUNDS)) - 1]
            if key:
                threads[key] = count


def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def canonical_by_domain(store):
    """Mail from an employer's own domain is that employer, however the name came
    out: ea.com yields "Ea" from the domain but "Electronic Arts" from a subject
    line, and folding on spelling alone never joins those. ATS and webmail
    domains carry many employers, so they are never grouped.

    A name somebody typed is not used here, only the ones derived from the
    mail. It outranks everything, so one correction took the domain and
    renamed every other application to the same employer: two applications to
    one company, a correction to one of them, and the other silently became
    "Acme Canada" too. A typed name still spreads through `canonical_names`,
    which joins spellings of *that* name - "ibm" to "IBM" - and that is the
    propagation the edit screen was built for. Crossing to a different name is
    not."""
    best = {}
    for row in store.values():
        name, address = row.get("company"), (row.get("from_address") or "").lower()
        if not name or "@" not in address or row.get("company_source") == "user":
            continue
        domain = SUBDOMAIN_NOISE.sub("", address.split("@")[-1])
        if domain in ATS_DOMAINS or any(domain.endswith("." + d) for d in ATS_DOMAINS):
            continue
        rank = spelling_rank(row)
        if domain not in best or rank < best[domain][0]:
            best[domain] = (rank, name)
    return {d: n for d, (_, n) in best.items()}


def canonical_names(store):
    """Map each folded company key to the best-spelled variant seen for it."""
    best = {}
    for row in store.values():
        name = row.get("company")
        if not name:
            continue
        key = name_key(name)
        rank = spelling_rank(row)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, name)
    return {key: name for key, (_, name) in best.items()}


def resolve_company(row, canon, by_domain):
    """The name this mail's employer is filed under, however the mail spelled it.

    The two maps answer different questions, and both are needed. `by_domain`
    joins names that never fold together - "Ea" from the domain and "Electronic
    Arts" from a subject line. `canon` then picks one spelling for that folded
    name, which is what stops a single application appearing as "Tucows" on its
    receipt and "Tucows Inc" on its rejection: without the second step the
    answer depends on which map happened to hold the row.
    """
    name = row.get("company") or "Unknown"
    address = (row.get("from_address") or "").lower()
    domain = SUBDOMAIN_NOISE.sub("", address.split("@")[-1]) if "@" in address else ""
    # A name somebody typed for this message is the answer for this message.
    # Everything else here derives a name from mail that the reader has just
    # overruled, and letting the domain overrule them back discards the
    # correction entirely - which it did, silently, the first time this tried
    # to stop a correction spreading.
    if row.get("company_source") != "user":
        # The domain's name still goes through the spelling fold:
        # thalesgroup.com yields "Thalesgroup", which is the same employer as a
        # Workday mail's "Thales".
        name = by_domain.get(domain) or name
    return canon.get(name_key(name), name)


# A requisition id wearing letters - "R0338030", "JR104221" - which
# `POSITION_NOISE` does not catch because it is not digits alone. It names the
# posting, not the job, so a variant carrying one is never the best name for it.
REQUISITION_TOKEN = re.compile(r"\b[A-Z]{1,3}\d{4,}\b")


def folds_by_company(store, canon, by_domain):
    """Per employer, which of its titles are the same role written shorter.

    See `rules.fold_position_keys`. Built once per report and consulted
    wherever a title becomes a key, so the list, the CSV and the page cannot
    disagree about which rows are one application.
    """
    titles = {}
    for row in store.values():
        position = row.get("position")
        if not position:
            continue
        titles.setdefault(name_key(resolve_company(row, canon, by_domain)), set()).add(position)
    return {company: fold_position_keys(seen) for company, seen in titles.items()}


def folded_key(company_key, position, folds):
    key = position_key(position)
    return folds.get(company_key, {}).get(key, key)


def canonical_positions(store, canon, by_domain, folds=None):
    """One role's title, spelled the way that says most about the job.

    The mails of a single application disagree about the decoration — the
    acknowledgement says "Senior Backend Engineer, AMER", the rejection just
    "Senior Backend Engineer" — and about how much of the title to keep at all:
    "Software Engineer" in one and "Software Engineer - Cloud Platform" in the
    next. They are one role, so the rows must read as one.

    Now that a group can hold "Software Engineer" beside "Software Engineer -
    Cloud Platform", the choice is the variant with the most words: the
    specialism is the part the reader applied under, and taking the shortest
    outright threw it away. Variants carrying a requisition id are passed over
    first - "Sr Software Developer (R0338030)" is not a better name for the
    job than "Sr Software Developer", and on one real mailbox that was the
    only thing the longest variant added. Then the one seen most often, then
    the shortest, as before.

    The cost of preferring more words is a title that keeps a scrap of the
    sentence it was lifted from: "Staff Software Engineer, Horizon team role"
    where "Staff Software Engineer, Horizon" was shorter and no less correct.
    That is a worse name for the same application, where the alternative was a
    worse name and a specialism lost.
    """
    folds = folds if folds is not None else {}
    counts = {}
    for row in store.values():
        position = row.get("position")
        if not position:
            continue
        company = name_key(resolve_company(row, canon, by_domain))
        key = (company, folded_key(company, position, folds))
        counts.setdefault(key, collections.Counter())[position] += 1
    return {key: min(seen.items(),
                     key=lambda kv: (1 if REQUISITION_TOKEN.search(kv[0]) else 0,
                                     -len(position_words(kv[0])), -kv[1],
                                     len(kv[0]), kv[0]))[0]
            for key, seen in counts.items()}


DEFAULT_REPEAT_WINDOW_DAYS = 20


def attempts(mails, window_days):
    """Which attempt at one role each of its mails belongs to.

    Takes the mails of a single employer + role in the order they arrived and
    returns a 1-based attempt number for each. A mail starts a new attempt when
    all three hold:

    - it is an acknowledgement. An application begins with a receipt and
      nothing else does; without this clause a rejection arriving two months
      after the receipt — ordinary employer behaviour — became an attempt of
      its own containing nothing but a rejection.
    - more than `window_days` have passed since the previous mail about this
      role, chained mail to mail rather than measured from the start, so an
      application whose stages are each three weeks apart stays one application
      however long it runs.
    - the attempt it would leave already has an acknowledgement. One receipt
      resent is still one application, and the stage-level collapse in
      `select_rows` is what handles that.
    """
    out = []
    attempt = 1
    previous = None
    has_receipt = False
    for received, status in mails:
        when = _parse(received)
        if (status == "Acknowledge" and has_receipt
                and previous is not None and when is not None
                and (when - previous).total_seconds() > window_days * 86400):
            attempt += 1
            has_receipt = False
        if status == "Acknowledge":
            has_receipt = True
        # An unparseable date cannot start an attempt, but it is still part of
        # the one it landed in.
        if when is not None:
            previous = when
        out.append(attempt)
    return out


def _parse(received):
    try:
        return dt.datetime.fromisoformat((received or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def warn_out_of_sequence(chosen):
    """An application runs acknowledge -> test/interview -> reject. A history
    that runs backwards means a mail is in the wrong stage, or two applications
    to one role got merged. It cannot be fixed by reordering, so it is reported.
    """
    by_role = {}
    for _, name, position, row, attempt in chosen:
        by_role.setdefault((name_key(name), position_key(position), attempt),
                           []).append((name, position, row))
    for group in by_role.values():
        group.sort(key=lambda e: (e[2]["date"], STAGE_ORDER.get(e[2]["status"], 9)))
        ranks = [STAGE_ORDER.get(row["status"], 9) for _, _, row in group]
        if ranks != sorted(ranks):
            name, position, _ = group[0]
            print("Warning: stages out of order for %s / %s: %s" % (
                name, position,
                " -> ".join("%s %s" % (r["date"], r["status"]) for _, _, r in group)),
                file=sys.stderr)


def select_rows(store, cutoff=None, repeat_window_days=DEFAULT_REPEAT_WINDOW_DAYS):
    """Choose which stored mails become CSV rows, and under which name and role.

    `cutoff` is the oldest ReceiveDate to publish. The store is durable and keeps
    everything, but mail that has aged out of the fetch window is never
    re-examined, so its verdict would otherwise sit in the CSV forever; widening
    lookback_days brings those rows straight back.

    `repeat_window_days` separates one event mailed twice from a second
    application to the same role. See `attempts`."""
    canon = canonical_names(store)
    by_domain = canonical_by_domain(store)
    fallback = backfill_positions(store, lambda row: resolve_company(row, canon, by_domain))
    unknowns = unknown_labels(store, lambda row: resolve_company(row, canon, by_domain))
    folds = folds_by_company(store, canon, by_domain)
    canon_position = canonical_positions(store, canon, by_domain, folds)

    by_role = {}
    for row in sorted(store.values(), key=lambda r: (r["received"], r.get("id") or "")):
        if row["status"] not in CSV_STATUSES:
            continue
        if cutoff and row["date"] < cutoff:
            continue
        name = resolve_company(row, canon, by_domain)
        # Presentation only — the store keeps a real null so a later run can
        # still backfill a title once one turns up in other mail.
        position = (row.get("position") or fallback.get(name_key(name))
                    or unknowns.get(row["id"]) or "Unknown")
        company_key = name_key(name)
        position = canon_position.get(
            (company_key, folded_key(company_key, position, folds)), position)
        # Sorted by arrival above, so each list is already in the order
        # `attempts` needs to read it.
        by_role.setdefault(
            (company_key, folded_key(company_key, position, folds)), []).append(
            (row["received"], name, position, row))

    # One row per employer + role + stage, within one attempt at that role. An
    # application reaches each stage once: a single receipt, one assessment
    # invite, one first-round invite, one rejection. Repeats are the same event
    # mailed twice — a resent rejection, an assessment announced then issued —
    # so the earliest is kept, being when that stage was actually reached.
    #
    # The key used to be employer + role + stage and nothing else, which has no
    # sense of time: an acknowledgement from one employer in August and a
    # second one forty days later were "the same receipt mailed twice", so the
    # October mail was dropped and the only sign of it was the list still
    # showing August. One real mailbox of 335 rows had swallowed three
    # applications that way.
    best = {}
    for role, entries in by_role.items():
        numbers = attempts([(e[0], e[3]["status"]) for e in entries], repeat_window_days)
        for (received, name, position, row), attempt in zip(entries, numbers):
            key = (role[0], role[1], attempt, row["status"])
            if key not in best or received < best[key][0]:
                best[key] = (received, name, position, row, attempt)

    # Grouped by employer, then role, then attempt, then oldest date first, so
    # one application's progression reads down the page in the order it
    # happened. Date before stage, so an acknowledgement and an invite that
    # arrive on the same day still read in the order they happened.
    chosen = sorted(best.values(),
                    key=lambda e: (e[1].casefold(), e[2].casefold(), e[4], e[3]["date"],
                                   STAGE_ORDER.get(e[3]["status"], 9)))
    return chosen


def write_csv(store, csv_path, cutoff=None,
              repeat_window_days=DEFAULT_REPEAT_WINDOW_DAYS):
    chosen = select_rows(store, cutoff, repeat_window_days)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["CompanyName", "Position", "ReceiveDate", "Status",
                         "Sender EmailAddress", "Subject", "Notes", "Web Link"])
        for _, name, position, row, _attempt in chosen:
            writer.writerow([name, position, row["date"], row["status"],
                             row.get("from_address") or "",
                             row.get("subject") or "",
                             row.get("evidence") or row.get("note") or "",
                             row.get("web_link") or ""])
    # The dedup key makes this true by construction; verified anyway so a future
    # change to the key cannot quietly reintroduce duplicate stages.
    warn_out_of_sequence(chosen)
    stages = [(name_key(n), position_key(pos), attempt, row["status"])
              for _, n, pos, row, attempt in chosen]
    if len(set(stages)) != len(stages):
        dupes = {k for k in stages if stages.count(k) > 1}
        raise AssertionError(
            "applications.csv would repeat a stage for one role: %s" % sorted(dupes))
    return len(chosen)


def cutoff(cfg):
    """Oldest ReceiveDate to publish. The store keeps everything, but mail that
    has aged out of the window is never re-examined, so a stale verdict would
    otherwise sit in the CSV for good. Widening lookback_days brings it back."""
    days = cfg.get("lookback_days")
    if not days:
        return None
    return (dt.date.today() - dt.timedelta(days=int(days))).isoformat()
