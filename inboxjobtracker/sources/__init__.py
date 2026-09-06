"""Mail sources. Each returns the same list of candidate dicts, so everything
downstream is identical no matter where the mail came from."""


def fetch(cfg, days):
    name = cfg.get("source", "demo")
    if name == "imap":
        from . import imap
        return imap.fetch(cfg, days)
    if name == "graph":
        from . import graph
        return graph.fetch(cfg, days)
    if name == "demo":
        from . import demo
        return demo.fetch(cfg, days)
    raise SystemExit(
        "Unknown source %r. Use one of: imap, graph, demo." % name)
