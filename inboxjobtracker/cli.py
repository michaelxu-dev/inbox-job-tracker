"""Command line: inbox-job-tracker run | fetch | classify | judge | merge | demo."""
import argparse
import json
import os
import sys

from . import __version__, config, html as html_mod, judge as judge_mod, report, rules, sources
from . import store as store_mod

FILES = ("candidates.json", "store.json", "review_queue.json",
         "decisions.json", "applications.csv", "applications.html")


def _paths(cfg):
    return {name: config.data_path(cfg, name) for name in FILES}


def cmd_fetch(cfg, args):
    days = args.days or int(cfg["lookback_days"])
    candidates, scanned = sources.fetch(cfg, days)
    path = _paths(cfg)["candidates.json"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"scanned": scanned, "candidates": candidates}, fh,
                  indent=2, ensure_ascii=False)
    print("Wrote %s (%d candidates from %d messages)"
          % (path, len(candidates), scanned), file=sys.stderr)
    return 0


# What it costs to have been wrong about a verdict nobody checked. An invented
# interview or a missed rejection changes what the reader believes about a real
# application; a receipt read as a receipt does not.
AUDIT_PRIORITY = {status: 0 for status in rules.INTERVIEW_ROUNDS}
AUDIT_PRIORITY.update({rules.INTERVIEW: 0, rules.TEST: 1, rules.REJECT: 2,
                       rules.ACK: 3})


def build_queue(review, audit, batch_size, audit_share):
    """Fill one review batch from both tiers, keeping a share for the audit.

    Concatenating the two and truncating starved the audit completely: there are
    always more uncertain items than a batch holds, so the confident verdicts
    sat at the back of the list and were never once reviewed. That is the wrong
    half to skip - the rules ask for help when they are unsure, and say nothing
    when they are confidently wrong. So the audit gets a reserved share, and
    each tier may take what the other leaves unused.
    """
    batch_size = max(0, batch_size)
    reserved = min(len(audit), max(1, round(batch_size * audit_share)) if audit else 0)
    chosen_review = review[:batch_size - reserved]
    # Riskiest verdicts first, so a small audit share is spent where being
    # wrong costs most rather than on acknowledgements.
    audit = sorted(audit, key=lambda item: AUDIT_PRIORITY.get(item.get("rule_status"), 9))
    chosen_audit = audit[:batch_size - len(chosen_review)]
    return chosen_review + chosen_audit


def cmd_classify(cfg, args):
    paths = _paths(cfg)
    if not os.path.exists(paths["candidates.json"]):
        sys.exit("No candidates yet - run:  inbox-job-tracker fetch")
    with open(paths["candidates.json"], "r", encoding="utf-8") as fh:
        candidates = json.load(fh)["candidates"]

    store = store_mod.load(paths["store.json"])
    own = set(cfg["own_addresses"])
    review, audit, counts = [], [], {}

    for cand in candidates:
        prior = store.get(cand["id"])

        # Who sent a mail is a fact, not a reading of it, so this outranks even
        # a judgement already made.
        if rules.is_own_address(cand["from_address"], own):
            record = dict(prior or {})
            record.update({
                "id": cand["id"], "status": rules.UNCLEAR, "confidence": "high",
                "note": "sent from your own address, not an employer reply",
                "decided_by": "rule:own-address", "subject": cand["subject"],
                "from_name": cand["from_name"], "from_address": cand["from_address"],
                "date": rules.local_date(cand["received"]), "received": cand["received"],
                "folder": cand.get("folder", ""), "web_link": cand.get("web_link"),
            })
            record.setdefault("company", None)
            record.setdefault("company_source", "unknown")
            store[cand["id"]] = record
            counts[rules.UNCLEAR] = counts.get(rules.UNCLEAR, 0) + 1
            continue

        if prior and prior.get("decided_by") == "agent":
            counts[prior["status"]] = counts.get(prior["status"], 0) + 1
            # The verdict is the judge's, but position and evidence are derived
            # from the mail, so they refresh whenever the rules improve.
            if prior.get("position_source") != "agent":
                prior["position"] = rules.position_from_text(
                    cand["subject"], cand.get("body", ""))
            prior["evidence"] = rules.evidence_sentence(
                cand["subject"], cand.get("body", ""), prior["status"])
            # The link is built by the source, so a fix to it reaches even rows
            # whose verdict is settled.
            prior["web_link"] = cand.get("web_link")
            continue

        verdict = rules.classify(cand)
        company, company_source = rules.guess_company(cand)
        record = {
            "id": cand["id"], "company": company, "company_source": company_source,
            "position": rules.position_from_text(cand["subject"], cand.get("body", "")),
            "evidence": rules.evidence_sentence(cand["subject"], cand.get("body", ""),
                                                verdict["status"]),
            "date": rules.local_date(cand["received"]), "received": cand["received"],
            "status": verdict["status"], "confidence": verdict["confidence"],
            "note": verdict["note"], "explicit_round": verdict["explicit_round"],
            "subject": cand["subject"], "from_name": cand["from_name"],
            "from_address": cand["from_address"], "folder": cand.get("folder", ""),
            "web_link": cand.get("web_link"), "decided_by": "rules",
            "scores": {"reject": verdict["reject_score"], "next": verdict["next_score"]},
        }
        store[cand["id"]] = record
        counts[record["status"]] = counts.get(record["status"], 0) + 1

        # A missing company only matters for a row that is going to the CSV.
        # Requiring one of every verdict queued a confident Unclear - a
        # newsletter, an account notice - for review purely because no employer
        # could be derived from it, which is exactly what should be expected.
        needs_company = record["status"] in rules.CSV_STATUSES and not company
        if verdict["confidence"] == "low" or needs_company:
            review.append(rules.review_item(
                cand, record, verdict, company, "uncertain",
                verdict["note"] or "no company name could be derived"))
        elif record["status"] in rules.CSV_STATUSES and not record.get("audited"):
            # Being confident is how the rules have been wrong before, so a
            # slice of confident verdicts is checked each run too.
            audit.append(rules.review_item(
                cand, record, verdict, company, "audit",
                "confident rule verdict, never checked"))

    queue = build_queue(review, audit, int(cfg["review_batch_size"]),
                        float(cfg.get("audit_share", 0.25)))
    report.assign_interview_rounds(store, int(cfg["interview_round_gap_days"]))
    store_mod.save(paths["store.json"], store)
    with open(paths["review_queue.json"], "w", encoding="utf-8") as fh:
        json.dump({"items": queue}, fh, indent=2, ensure_ascii=False)

    written = report.write_csv(store, paths["applications.csv"], report.cutoff(cfg))
    # Written together so the page can never quietly disagree with the CSV.
    html_mod.write_html(store, paths["applications.html"], report.cutoff(cfg),
                        cfg.get("account"))
    print("Classified %d: %s" % (len(candidates),
          ", ".join("%s=%d" % kv for kv in sorted(counts.items()))), file=sys.stderr)
    print("CSV rows: %d | queued for review: %d" % (written, len(queue)), file=sys.stderr)
    return 0


def cmd_html(cfg, args):
    """The same rows as the CSV, grouped into one entry per application."""
    paths = _paths(cfg)
    store = store_mod.load(paths["store.json"])
    if not store:
        sys.exit("Nothing in the store yet - run:  inbox-job-tracker run")
    count = html_mod.write_html(store, paths["applications.html"],
                                report.cutoff(cfg), cfg.get("account"))
    print("Wrote %s (%d applications)" % (paths["applications.html"], count),
          file=sys.stderr)
    return 0


def cmd_merge(cfg, args):
    paths = _paths(cfg)
    if not os.path.exists(paths["decisions.json"]):
        print("No decisions.json to merge.", file=sys.stderr)
        return 0
    store = store_mod.load(paths["store.json"])
    with open(paths["decisions.json"], "r", encoding="utf-8") as fh:
        decisions = json.load(fh)
    # The evidence sentence is the one the *old* status rested on. Overturning a
    # verdict without re-deriving it left the Notes column quoting the losing
    # argument: an Acknowledge read off "Thanks for your interest in <company>"
    # stayed in the notes after the mail was correctly called a rejection.
    bodies = {}
    if os.path.exists(paths["candidates.json"]):
        with open(paths["candidates.json"], "r", encoding="utf-8") as fh:
            bodies = {c["id"]: c for c in json.load(fh)["candidates"]}
    applied = 0
    for dec in decisions:
        rec = store.get(dec.get("id"))
        if not rec:
            continue
        if dec.get("company"):
            rec["company"], rec["company_source"] = dec["company"], "agent"
        if dec.get("position"):
            rec["position"], rec["position_source"] = dec["position"], "agent"
        was = rec.get("status")
        rec["status"] = store_mod.LEGACY_STATUS.get(dec["status"], dec["status"])
        rec["agent_status"] = rec["status"]
        rec["confidence"] = "agent"
        rec["note"] = dec.get("note", "")
        rec["decided_by"] = "agent"
        rec["audited"] = True
        cand = bodies.get(dec["id"])
        if cand:
            rec["evidence"] = rules.evidence_sentence(
                cand.get("subject"), cand.get("body"), rec["status"])
        elif was != rec["status"]:
            # Nothing to re-derive it from, so drop it rather than keep a
            # sentence that argues for the overturned verdict; the CSV falls
            # back to the note the judgement gave.
            rec["evidence"] = None
        applied += 1
    report.assign_interview_rounds(store, int(cfg["interview_round_gap_days"]))
    store_mod.save(paths["store.json"], store)
    written = report.write_csv(store, paths["applications.csv"], report.cutoff(cfg))
    html_mod.write_html(store, paths["applications.html"], report.cutoff(cfg),
                        cfg.get("account"))
    print("Merged %d decisions. CSV rows: %d" % (applied, written), file=sys.stderr)
    return 0


def cmd_judge(cfg, args):
    paths = _paths(cfg)
    if not judge_mod.available(cfg):
        print("LLM judge is off. The rules alone already wrote applications.csv.\n"
              "To enable it: set ANTHROPIC_API_KEY and judge to 'on' in config.json.",
              file=sys.stderr)
        return 0
    with open(paths["review_queue.json"], "r", encoding="utf-8") as fh:
        items = json.load(fh)["items"]
    if not items:
        print("Nothing queued for review.", file=sys.stderr)
        return 0
    print("Asking %s to read %d messages..." % (cfg["judge_model"], len(items)),
          file=sys.stderr)
    decisions = judge_mod.judge(cfg, items)
    with open(paths["decisions.json"], "w", encoding="utf-8") as fh:
        json.dump(list(decisions.values()), fh, indent=2, ensure_ascii=False)
    return cmd_merge(cfg, args)


def cmd_run(cfg, args):
    cmd_fetch(cfg, args)
    cmd_classify(cfg, args)
    if judge_mod.available(cfg):
        cmd_judge(cfg, args)
    paths = _paths(cfg)
    print("\nDone -> %s\n        %s"
          % (paths["applications.csv"], paths["applications.html"]), file=sys.stderr)
    return 0


def cmd_demo(cfg, args):
    cfg = dict(cfg, source="demo")
    cmd_fetch(cfg, args)
    cmd_classify(cfg, args)
    print("", file=sys.stderr)
    paths = _paths(cfg)
    with open(paths["applications.csv"], "r", encoding="utf-8-sig") as fh:
        sys.stdout.write(fh.read())
    # stdout is block-buffered when piped, so without this the trailing line on
    # stderr overtakes the CSV and prints first.
    sys.stdout.flush()
    # The CSV is on screen already; the page is the part worth opening, and a
    # relative path is not clickable in most terminals.
    print("\nThe same rows as a page - open this in a browser:\n  %s"
          % os.path.abspath(paths["applications.html"]), file=sys.stderr)
    return 0


def cmd_accounts(cfg, args):
    names = config.accounts(args.config)
    if not names:
        print("%s defines no accounts block; it configures a single mailbox."
              % args.config, file=sys.stderr)
        return 0
    for name in names:
        one = config.load(args.config, name)
        print("%-10s %-6s %-34s -> %s%s"
              % (name, one["source"],
                 one.get("imap_user") or one.get("client_id") or "",
                 one["data_dir"],
                 "   (default)" if name == cfg.get("account") else ""))
    return 0


COMMANDS = (
    ("run", cmd_run, "fetch, classify, and judge if enabled"),
    ("fetch", cmd_fetch, "read mail into candidates.json"),
    ("classify", cmd_classify, "apply the rules, write applications.csv"),
    ("judge", cmd_judge, "ask an LLM about the uncertain ones (needs an API key)"),
    ("merge", cmd_merge, "fold decisions.json into the store"),
    ("html", cmd_html, "write applications.html, grouped by application"),
    ("demo", cmd_demo, "run on synthetic emails, no mailbox needed"),
    ("accounts", cmd_accounts, "list the mailboxes this config defines"),
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="inbox-job-tracker",
        description="Turn job-application replies in your mailbox into a spreadsheet.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-c", "--config", default="config.json",
                        help="path to config.json (default: ./config.json)")
    parser.add_argument("-a", "--account",
                        help="which mailbox in config.json to use (default: "
                             "default_account, else the first one defined)")
    parser.add_argument("-d", "--days", type=int,
                        help="days of mail to scan (default: lookback_days in config). A bare number is shorthand: 'inbox-job-tracker 7'")
    # Accepted on either side of the subcommand, because both readings are
    # natural: "run --days 7" and "--days 7 run". SUPPRESS keeps an absent
    # subcommand flag from blanking one given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-d", "--days", type=int, default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("-c", "--config", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("-a", "--account", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")
    for name, func, help_text in COMMANDS:
        sub.add_parser(name, help=help_text, parents=[common]).set_defaults(func=func)

    argv = list(sys.argv[1:] if argv is None else argv)
    # "inbox-job-tracker 7" is the obvious way to ask for the last seven days,
    # so treat a bare number as shorthand for the full run over that window.
    if argv and argv[0].isdigit():
        # --days is a top-level option, so it has to precede the subcommand.
        argv = ["--days", argv[0]] + argv[1:] + ["run"]

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.func(config.load(args.config, args.account), args)


if __name__ == "__main__":
    sys.exit(main())
