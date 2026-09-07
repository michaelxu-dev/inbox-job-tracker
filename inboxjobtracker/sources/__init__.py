"""Mail sources. Each returns the same list of candidate dicts, so everything
downstream is identical no matter where the mail came from."""


def fetch(cfg, days):
    name = cfg.get("source", "demo")
    if name == "imap":
        from . import imap
        return imap.fetch(cfg, days)
    if name == "graph":
        try:
            from . import graph
        except ImportError as exc:
            # Kept out of the core install so the rules and IMAP need nothing at
            # all; say which command fixes it rather than raising a bare
            # ModuleNotFoundError at someone who just set source to "graph".
            raise SystemExit(
                'The "graph" source needs two extra packages (%s).\n'
                '  pip install -e ".[graph]"     # or: pip install msal requests'
                % exc.name)
        return graph.fetch(cfg, days)
    if name == "demo":
        from . import demo
        return demo.fetch(cfg, days)
    raise SystemExit(
        "Unknown source %r. Use one of: imap, graph, demo." % name)
