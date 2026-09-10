"""Rendering the store into a self-contained applications.html.

The CSV is one row per stage, which is the right shape for a spreadsheet and the
wrong one for reading: an application's history arrives as three or four rows you
have to reassemble by eye, and the evidence sentence - the whole reason to trust
a verdict - is a truncated cell you have to widen a column to see.

So the page groups by application and shows the stages as a timeline, with the
sentence each verdict rests on and a link back to the message. No dependencies,
no CDN, no server: one file you open in a browser, which matters because the
contents are your correspondence and should not need a network to read.
"""
import datetime as dt
import html
import io
import json
import os

from .rules import STAGE_ORDER, name_key, position_key
from .report import select_rows


# Waiting is not a status any mail carries: it is what an application looks like
# when the only thing that ever arrived was the receipt.
WAITING = "No reply yet"

STATUS_CLASS = {
    "Acknowledge": "ack",
    "Invite to test": "test",
    "Invite to first interview": "iv",
    "Invite to second interview": "iv",
    "Invite to third interview": "iv",
    "Reject": "rej",
    WAITING: "wait",
}


def group_applications(rows):
    """One entry per employer + role, with its stages oldest first.

    `select_rows` already collapsed repeats and ordered the rows; this only
    gathers the stages of one application back together, which is what the CSV
    cannot express and the reason this page exists.
    """
    apps = {}
    for _, company, position, row in rows:
        key = (name_key(company), position_key(position))
        app = apps.setdefault(key, {
            "company": company, "position": position, "stages": [],
        })
        app["stages"].append({
            "status": row["status"],
            "date": row["date"],
            "sender": row.get("from_address") or "",
            "subject": row.get("subject") or "",
            "evidence": row.get("evidence") or row.get("note") or "",
            "link": row.get("web_link") or "",
            "by": row.get("decided_by") or "rules",
        })

    out = []
    for app in apps.values():
        app["stages"].sort(key=lambda s: (s["date"], STAGE_ORDER.get(s["status"], 9)))
        # The stage an application actually reached, which is not the last mail:
        # a rejection arrives after an interview and would otherwise hide it.
        furthest = max(app["stages"], key=lambda s: STAGE_ORDER.get(s["status"], 9))
        app["reached"] = furthest["status"]
        app["last"] = max(s["date"] for s in app["stages"])
        app["first"] = min(s["date"] for s in app["stages"])
        # An application whose only mail is a receipt is still open, and that is
        # the number people actually want: how many am I still waiting on.
        if {s["status"] for s in app["stages"]} == {"Acknowledge"}:
            app["reached"] = WAITING
        out.append(app)
    out.sort(key=lambda a: (a["last"], a["company"].casefold()), reverse=True)
    return out


def summarise(apps):
    """The counts worth putting at the top of the page."""
    advanced = [a for a in apps
                if a["reached"].startswith("Invite") or a["reached"] == "Reject"]
    interviews = [a for a in apps if a["reached"].startswith("Invite")]
    return [
        ("Applications", len(apps)),
        ("Employers", len({name_key(a["company"]) for a in apps})),
        ("Interviews or tests", len(interviews)),
        ("Rejections", len([a for a in apps if a["reached"] == "Reject"])),
        ("No reply yet", len([a for a in apps if a["reached"] == WAITING])),
        # Any answer beyond the automatic receipt counts as a reply - a rejection
        # is a reply. Rate over applications, not over messages.
        ("Reply rate", "%d%%" % round(100.0 * len(advanced) / len(apps)) if apps else "-"),
    ]


CSS = """
:root {
  --bg:#f7f7f8; --panel:#fff; --ink:#1a1a1c; --muted:#6b6b73; --line:#e3e3e8;
  --accent:#2a5bd7; --chip:#eeeef2;
  --ack:#5b3fa8;  --ack-bg:#ece5fa;
  --test:#8a5a00; --test-bg:#fdf1dc;
  --iv:#0f7b4f;   --iv-bg:#e4f5ec;
  --rej:#8a1c1c;  --rej-bg:#fbe9e9;
  --wait:#6b6b73; --wait-bg:#eeeef2;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#141416; --panel:#1c1c20; --ink:#e9e9ec; --muted:#9a9aa4; --line:#2c2c33;
    --accent:#7aa2ff; --chip:#26262c;
    --ack:#b9a3f0;  --ack-bg:#2a2140;
    --test:#e8b45c; --test-bg:#3a2c12;
    --iv:#5fd4a0;   --iv-bg:#123528;
    --rej:#f08989;  --rej-bg:#3a1717;
    --wait:#9a9aa4; --wait-bg:#26262c;
  }
}
* { box-sizing:border-box; }
body { margin:0; padding:28px 22px 60px; background:var(--bg); color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
h1 { font-size:20px; margin:0 0 4px; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
.stats { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }
.stat { background:var(--panel); border:1px solid var(--line); border-radius:9px;
  padding:9px 14px; min-width:104px; }
.stat b { display:block; font-size:19px; line-height:1.2; font-variant-numeric:tabular-nums; }
.stat span { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
.controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:14px; }
input[type=search] { flex:1 1 220px; min-width:190px; padding:8px 11px; border-radius:8px;
  border:1px solid var(--line); background:var(--panel); color:var(--ink); font-size:13px; }
.chip { padding:6px 12px; border-radius:999px; border:1px solid var(--line);
  background:var(--chip); color:var(--ink); cursor:pointer; font-size:12.5px; user-select:none; }
.chip[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
.wrap { overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:11px; }
table { border-collapse:collapse; width:100%; min-width:760px; }
th, td { padding:9px 12px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }
th { position:sticky; top:0; background:var(--panel); cursor:pointer; white-space:nowrap;
  font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); z-index:1; }
th:hover { color:var(--ink); }
tbody tr.app { cursor:pointer; }
tbody tr.app:hover { background:var(--chip); }
td.co { font-weight:600; white-space:nowrap; }
td.when { color:var(--muted); white-space:nowrap; font-variant-numeric:tabular-nums; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.tag { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11.5px;
  font-weight:600; white-space:nowrap; }
.t-ack{color:var(--ack);background:var(--ack-bg);}
.t-test{color:var(--test);background:var(--test-bg);}
.t-iv{color:var(--iv);background:var(--iv-bg);}
.t-rej{color:var(--rej);background:var(--rej-bg);}
.t-wait{color:var(--wait);background:var(--wait-bg);}
.trail { display:flex; flex-wrap:wrap; gap:5px; align-items:center; }
.trail .sep { color:var(--muted); font-size:11px; }
.detail td { background:var(--bg); }
.step { display:grid; grid-template-columns:96px 1fr; gap:10px; padding:7px 0;
  border-top:1px dashed var(--line); }
.step:first-child { border-top:0; }
.step .d { color:var(--muted); font-variant-numeric:tabular-nums; font-size:12.5px; }
.quote { margin:3px 0 0; padding-left:9px; border-left:2px solid var(--line);
  color:var(--muted); font-size:12.5px; }
.meta { color:var(--muted); font-size:11.5px; margin-top:3px; }
.by { font-size:10px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
.empty { padding:26px; text-align:center; color:var(--muted); }
.foot { color:var(--muted); font-size:11.5px; margin-top:16px; }
"""

# Kept ES5 and framework-free on purpose: the page has to open from a file:// URL
# on whatever browser is to hand, years from now, with no network.
JS = """
var rows = DATA.apps, q = document.getElementById("q"), tb = document.getElementById("tb"),
    empty = document.getElementById("empty"), filter = null, sortKey = null, sortDir = 1;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
  });
}
function haystack(a) {
  var t = a.company + " " + a.position + " " + a.reached;
  for (var i = 0; i < a.stages.length; i++) {
    t += " " + a.stages[i].subject + " " + a.stages[i].evidence + " " + a.stages[i].sender;
  }
  return t.toLowerCase();
}
function match(a) {
  if (filter && a.reached !== filter) return false;
  var t = q.value.trim().toLowerCase();
  return !t || haystack(a).indexOf(t) !== -1;
}
function tag(status) {
  return '<span class="tag t-' + esc(DATA.cls[status] || "wait") + '">' + esc(status) + "</span>";
}
function trail(a) {
  var out = [];
  for (var i = 0; i < a.stages.length; i++) {
    if (i) out.push('<span class="sep">&rsaquo;</span>');
    out.push(tag(a.stages[i].status));
  }
  return '<div class="trail">' + out.join("") + "</div>";
}
function steps(a) {
  return a.stages.map(function (s) {
    var head = s.link ? '<a href="' + esc(s.link) + '" target="_blank" rel="noopener">'
                        + esc(s.subject || "(no subject)") + "</a>"
                      : esc(s.subject || "(no subject)");
    var quote = s.evidence ? '<p class="quote">' + esc(s.evidence) + "</p>" : "";
    return '<div class="step"><div class="d">' + esc(s.date) + "</div><div>"
         + tag(s.status) + " " + head + quote
         + '<div class="meta">' + esc(s.sender)
         + ' &middot; <span class="by">decided by ' + esc(s.by) + "</span></div>"
         + "</div></div>";
  }).join("");
}
function render() {
  var out = rows.filter(match);
  if (sortKey) {
    out = out.slice().sort(function (x, y) {
      var a = x[sortKey], b = y[sortKey];
      if (sortKey === "reached") { a = DATA.order[a]; b = DATA.order[b]; return (a - b) * sortDir; }
      return String(a).localeCompare(String(b), undefined, { numeric: true }) * sortDir;
    });
  }
  empty.hidden = out.length > 0;
  tb.innerHTML = out.map(function (a, i) {
    return '<tr class="app" data-i="' + i + '">'
         + '<td class="co">' + esc(a.company) + "</td>"
         + "<td>" + esc(a.position) + "</td>"
         + "<td>" + tag(a.reached) + "</td>"
         + "<td>" + trail(a) + "</td>"
         + '<td class="when">' + esc(a.last) + "</td></tr>"
         + '<tr class="detail" hidden><td colspan="5">' + steps(a) + "</td></tr>";
  }).join("");
  var trs = tb.querySelectorAll("tr.app");
  for (var i = 0; i < trs.length; i++) {
    trs[i].onclick = function () {
      var d = this.nextElementSibling;
      d.hidden = !d.hidden;
    };
  }
}
q.oninput = render;
var chips = document.querySelectorAll(".chip");
for (var i = 0; i < chips.length; i++) {
  chips[i].onclick = function () {
    var want = this.getAttribute("data-status");
    filter = (filter === want) ? null : want;
    for (var j = 0; j < chips.length; j++) {
      chips[j].setAttribute("aria-pressed",
        String(chips[j].getAttribute("data-status") === filter));
    }
    render();
  };
}
var ths = document.querySelectorAll("th[data-key]");
for (var k = 0; k < ths.length; k++) {
  ths[k].onclick = function () {
    var key = this.getAttribute("data-key");
    sortDir = (sortKey === key) ? -sortDir : 1;
    sortKey = key;
    render();
  };
}
render();
"""


def render(apps, account, source_rows):
    """The whole page as one string. Data goes in as JSON, never as markup, so a
    subject line full of angle brackets cannot break the table."""
    e = html.escape
    stats = "".join(
        '<div class="stat"><b>%s</b><span>%s</span></div>' % (e(str(value)), e(label))
        for label, value in summarise(apps))
    # Only offer a filter for statuses that are actually present.
    present = []
    for status in ["No reply yet", "Invite to test", "Invite to first interview",
                   "Invite to second interview", "Invite to third interview", "Reject"]:
        if any(a["reached"] == status for a in apps):
            present.append(status)
    chips = "".join(
        '<button class="chip" data-status="%s" aria-pressed="false">%s</button>'
        % (e(status), e(status)) for status in present)

    payload = {
        "apps": apps,
        "cls": STATUS_CLASS,
        "order": {s: i for s, i in STAGE_ORDER.items()},
    }
    payload["order"][WAITING] = -1
    # </script> inside a string would end the block early; the escape is cheap
    # insurance on data that came out of somebody's mailbox.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Application history%(titleacct)s</title>
<style>%(css)s</style>
</head>
<body>
<h1>Application history</h1>
<div class="sub">%(count)d applications from %(rows)d recorded stages%(acct)s &middot;
generated %(now)s &middot; click a row for the messages behind it</div>
<div class="stats">%(stats)s</div>
<div class="controls">
  <input type="search" id="q" placeholder="Search company, role, subject or quoted sentence">
  %(chips)s
</div>
<div class="wrap">
<table>
<thead><tr>
  <th data-key="company">Company</th>
  <th data-key="position">Role</th>
  <th data-key="reached">Stage reached</th>
  <th>History</th>
  <th data-key="last">Last activity</th>
</tr></thead>
<tbody id="tb"></tbody>
</table>
<div class="empty" id="empty" hidden>Nothing matches that filter.</div>
</div>
<p class="foot">Generated from store.json. Nothing here left your machine; the links
open the original message in your mail client.</p>
<script>var DATA = %(data)s;</script>
<script>%(js)s</script>
</body>
</html>
""" % {
        "css": CSS, "js": JS, "data": data, "stats": stats, "chips": chips,
        "count": len(apps), "rows": source_rows,
        "acct": (" &middot; %s" % e(account)) if account else "",
        "titleacct": (" - %s" % e(account)) if account else "",
        "now": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def write_html(store, path, cutoff=None, account=None):
    rows = select_rows(store, cutoff)
    apps = group_applications(rows)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(apps, account, len(rows)))
    return len(apps)
