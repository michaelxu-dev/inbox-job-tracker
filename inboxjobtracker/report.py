"""Turning the classified store into applications.csv.

One row per employer + role + status: an application reaches each stage once, so
a resent rejection or an assessment announced and then issued collapse to the
earliest mail, which is when that stage was actually reached.
"""
import csv
import datetime as dt
import json
import re

from .rules import (ACK, ATS_DOMAINS, backfill_positions, name_key,
                    position_key, CSV_STATUSES, INTERVIEW, INTERVIEW_ROUNDS,
                    REJECT, STAGE_ORDER, SUBDOMAIN_NOISE)


SOURCE_RANK = {"agent": 0, "text": 1, "sender-name": 2, "domain": 3,
               "ats-localpart": 4, "unknown": 5}


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
                count, round_start = stated, date
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
    domains carry many employers, so they are never grouped."""
    best = {}
    for row in store.values():
        name, address = row.get("company"), (row.get("from_address") or "").lower()
        if not name or "@" not in address:
            continue
        domain = SUBDOMAIN_NOISE.sub("", address.split("@")[-1])
        if domain in ATS_DOMAINS or any(domain.endswith("." + d) for d in ATS_DOMAINS):
            continue
        rank = SOURCE_RANK.get(row.get("company_source"), 4)
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
        rank = SOURCE_RANK.get(row.get("company_source"), 4)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, name)
    return {key: name for key, (_, name) in best.items()}


def warn_out_of_sequence(chosen):
    """An application runs acknowledge -> test/interview -> reject. A history
    that runs backwards means a mail is in the wrong stage, or two applications
    to one role got merged. It cannot be fixed by reordering, so it is reported.
    """
    by_role = {}
    for _, name, position, row in chosen:
        by_role.setdefault((name_key(name), position_key(position)), []).append((name, position, row))
    for group in by_role.values():
        group.sort(key=lambda e: (e[2]["date"], STAGE_ORDER.get(e[2]["status"], 9)))
        ranks = [STAGE_ORDER.get(row["status"], 9) for _, _, row in group]
        if ranks != sorted(ranks):
            name, position, _ = group[0]
            print("Warning: stages out of order for %s / %s: %s" % (
                name, position,
                " -> ".join("%s %s" % (r["date"], r["status"]) for _, _, r in group)),
                file=sys.stderr)


def select_rows(store, cutoff=None):
    """Choose which stored mails become CSV rows, and under which name and role.

    `cutoff` is the oldest ReceiveDate to publish. The store is durable and keeps
    everything, but mail that has aged out of the fetch window is never
    re-examined, so its verdict would otherwise sit in the CSV forever; widening
    lookback_days brings those rows straight back."""
    canon = canonical_names(store)
    by_domain = canonical_by_domain(store)
    fallback = backfill_positions(store)

    # One row per employer + role + date, so an application keeps its history:
    # a first interview in August and a Reject in September are both kept, while
    # the several mails of one interview loop on one day collapse. An Acknowledge
    # and a real decision can also share a day, and the key holds no status, so
    # the decision is preferred over the receipt; otherwise the latest mail wins.
    best = {}
    for row in store.values():
        if row["status"] not in CSV_STATUSES:
            continue
        if cutoff and row["date"] < cutoff:
            continue
        name = row["company"] or "Unknown"
        address = (row.get("from_address") or "").lower()
        domain = SUBDOMAIN_NOISE.sub("", address.split("@")[-1]) if "@" in address else ""
        name = by_domain.get(domain) or canon.get(name_key(name), name)
        # Presentation only — the store keeps a real null so a later run can
        # still backfill a title once one turns up in other mail.
        position = row.get("position") or fallback.get(name_key(name)) or "Unknown"
        # One row per employer + role + status. An application reaches each
        # stage once: a single receipt, one assessment invite, one first-round
        # invite, one rejection. Repeats are the same event mailed twice — a
        # resent rejection, an assessment announced then issued — so the
        # earliest is kept, being when that stage was actually reached.
        key = (name_key(name), position_key(position), row["status"])
        if key not in best or row["received"] < best[key][0]:
            best[key] = (row["received"], name, position, row)

    # Grouped by employer, then role, then oldest date first, so one
    # application's progression reads down the page in the order it happened.
    # Date first, then stage, so an acknowledgement and an invite that arrive on
    # the same day still read in the order they happened.
    chosen = sorted(best.values(),
                    key=lambda e: (e[1].casefold(), e[2].casefold(), e[3]["date"],
                                   STAGE_ORDER.get(e[3]["status"], 9)))
    return chosen


def write_csv(store, csv_path, cutoff=None):
    chosen = select_rows(store, cutoff)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["CompanyName", "Position", "ReceiveDate", "Status",
                         "Sender EmailAddress", "Subject", "Notes", "Web Link"])
        for _, name, position, row in chosen:
            writer.writerow([name, position, row["date"], row["status"],
                             row.get("from_address") or "",
                             row.get("subject") or "",
                             row.get("evidence") or row.get("note") or "",
                             row.get("web_link") or ""])
    # The dedup key makes this true by construction; verified anyway so a future
    # change to the key cannot quietly reintroduce duplicate stages.
    warn_out_of_sequence(chosen)
    stages = [(name_key(n), position_key(pos), row["status"]) for _, n, pos, row in chosen]
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
