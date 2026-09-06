"""Command line: inbox-job-tracker run | fetch | classify | judge | merge | demo."""
import argparse
import json
import os
import sys

from . import __version__, config, judge as judge_mod, report, rules, sources
from . import store as store_mod

FILES = ("candidates.json", "store.json", "review_queue.json",
         "decisions.json", "applications.csv")


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

        if verdict["confidence"] == "low" or not company:
            review.append(rules.review_item(
                cand, record, verdict, company, "uncertain",
                verdict["note"] or "no company name could be derived"))
        elif record["status"] in rules.CSV_STATUSES and not record.get("audited"):
            # Being confident is how the rules have been wrong before, so a
            # slice of confident verdicts is checked each run too.
            audit.append(rules.review_item(
                cand, record, verdict, company, "audit",
                "confident rule verdict, never checked"))

    queue = (review + audit)[:int(cfg["review_batch_size"])]
    report.assign_interview_rounds(store, int(cfg["interview_round_gap_days"]))
    store_mod.save(paths["store.json"], store)
    with open(paths["review_queue.json"], "w", encoding="utf-8") as fh:
        json.dump({"items": queue}, fh, indent=2, ensure_ascii=False)

    written = report.write_csv(store, paths["applications.csv"], report.cutoff(cfg))
    print("Classified %d: %s" % (len(candidates),
          ", ".join("%s=%d" % kv for kv in sorted(counts.items()))), file=sys.stderr)
    print("CSV rows: %d | queued for review: %d" % (written, len(queue)), file=sys.stderr)
    return 0


def cmd_merge(cfg, args):
    paths = _paths(cfg)
    if not os.path.exists(paths["decisions.json"]):
        print("No decisions.json to merge.", file=sys.stderr)
        return 0
    store = store_mod.load(paths["store.json"])
    with open(paths["decisions.json"], "r", encoding="utf-8") as fh:
        decisions = json.load(fh)
    applied = 0
    for dec in decisions:
        rec = store.get(dec.get("id"))
        if not rec:
            continue
        if dec.get("company"):
            rec["company"], rec["company_source"] = dec["company"], "agent"
        if dec.get("position"):
            rec["position"], rec["position_source"] = dec["position"], "agent"
        rec["status"] = store_mod.LEGACY_STATUS.get(dec["status"], dec["status"])
        rec["agent_status"] = rec["status"]
        rec["confidence"] = "agent"
        rec["note"] = dec.get("note", "")
        rec["decided_by"] = "agent"
        rec["audited"] = True
        applied += 1
    report.assign_interview_rounds(store, int(cfg["interview_round_gap_days"]))
    store_mod.save(paths["store.json"], store)
    written = report.write_csv(store, paths["applications.csv"], report.cutoff(cfg))
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
    print("\nDone -> %s" % _paths(cfg)["applications.csv"], file=sys.stderr)
    return 0


def cmd_demo(cfg, args):
    cfg = dict(cfg, source="demo")
    cmd_fetch(cfg, args)
    cmd_classify(cfg, args)
    print("", file=sys.stderr)
    with open(_paths(cfg)["applications.csv"], "r", encoding="utf-8-sig") as fh:
        sys.stdout.write(fh.read())
    return 0


COMMANDS = (
    ("run", cmd_run, "fetch, classify, and judge if enabled"),
    ("fetch", cmd_fetch, "read mail into candidates.json"),
    ("classify", cmd_classify, "apply the rules, write applications.csv"),
    ("judge", cmd_judge, "ask an LLM about the uncertain ones (needs an API key)"),
    ("merge", cmd_merge, "fold decisions.json into the store"),
    ("demo", cmd_demo, "run on synthetic emails, no mailbox needed"),
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="inbox-job-tracker",
        description="Turn job-application replies in your mailbox into a spreadsheet.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-c", "--config", default="config.json",
                        help="path to config.json (default: ./config.json)")
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
    return args.func(config.load(args.config), args)


if __name__ == "__main__":
    sys.exit(main())
