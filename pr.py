"""
    pr_diff.py - Compare two `cred_pred.py --json` outputs and render a PR comment.
    python pr_diff.py base.json head.json --out comment.md [--fail-over 100]
"""
import argparse
import json
import sys
from pathlib import Path

MARKER = "<!-- cred-pred-comment -->"  # lets the workflow find and update its own comment


def load(path: str) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) and "resources" in d else {}


def covered(d: dict) -> float:
    return (d.get("credits") or {}).get("monthly_covered", 0.0) or 0.0


def by_name(d: dict) -> dict:
    return {r["name"]: r for r in d.get("resources", [])}


def usd(x: float, signed: bool = False) -> str:
    if signed:
        return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"
    return f"${x:,.2f}"


def runway_str(d: dict) -> str:
    r = (d.get("credits") or {}).get("runway_months")
    return "n/a" if r is None else f"{r} mo"


def build_comment(base: dict, head: dict) -> tuple[str, float]:
    b_cost, h_cost = covered(base), covered(head)
    delta = h_cost - b_cost
    hc = head.get("credits") or {}

    L = [MARKER, "## Credit Burn Check", ""]
    L.append(f"**Monthly burn:** {usd(b_cost)} -> **{usd(h_cost)}** ({usd(delta, True)}/mo)")
    L.append(f"**Runway:** {runway_str(base)} -> **{runway_str(head)}**"
             + (f" &nbsp;(runs out ~{hc['runs_out']})" if hc.get("runs_out") else ""))

    status = hc.get("status")
    if status == "expires_first":
        L.append(f"\n> **Credits expire first:** ~{usd(hc['wasted'])} will go unused at this burn.")
    elif status == "expired":
        L.append("\n> **Credits expired:** these credits have already expired.")

    # Per-resource changes (resources are keyed by Terraform address)
    b, h = by_name(base), by_name(head)
    rows = []
    for name in sorted(set(b) | set(h)):
        old = (b.get(name) or {}).get("monthly")
        new = (h.get(name) or {}).get("monthly")
        if name not in b:
            tag, d = "added", new
        elif name not in h:
            tag, d = "removed", -(old or 0)
        elif (old or 0) != (new or 0) or (old is None) != (new is None):
            tag, d = "changed", (new or 0) - (old or 0)
        else:
            continue
        shown = h.get(name) or b.get(name)
        rows.append((abs(d or 0), name, tag, shown.get("type", ""), shown.get("quantity", 1),
                     usd(d, True) if d is not None else "unpriced"))

    if rows:
        L += ["", "| Resource | Change | Type | Qty | Change $/mo |", "|---|---|---|---:|---:|"]
        for _, name, tag, typ, qty, d in sorted(rows, reverse=True):
            L.append(f"| `{name}` | {tag} | {typ} | {qty} | {d} |")
    else:
        L.append("\nNo cost-relevant infrastructure changes in this PR.")

    unpriced = [r["name"] for r in head.get("resources", []) if r.get("monthly") is None]
    if unpriced or head.get("unpriced_types"):
        L.append("\n<details><summary>Not fully priced</summary>\n")
        if unpriced:
            L.append(f"- Could not price: {', '.join(f'`{n}`' for n in unpriced)}")
        if head.get("unpriced_types"):
            L.append(f"- Unsupported resource types: {', '.join(head['unpriced_types'])}")
        L.append("- The real burn is **higher** than shown.\n\n</details>")

    tip = head.get("tip") or {}
    if tip.get("text"):
        who = tip["source"][4:] if tip.get("source", "").startswith("llm:") else "rules"
        L.append(f"\n**Tip** *(via {who})*: {tip['text']}")

    L.append("\n<sub>Numbers are computed from live AWS pricing; flat estimates are labelled in the full report. "
             "Credit balance is a static setting, not live spend.</sub>")
    return "\n".join(L), delta


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("head")
    p.add_argument("--out", default="comment.md")
    p.add_argument("--fail-over", type=float, default=None,
                   help="Exit 1 if the PR raises credit-covered burn by more than this many USD/month")
    args = p.parse_args()

    head = load(args.head)
    if not head:
        Path(args.out).write_text(f"{MARKER}\n## Credit Burn Check\n\nCould not estimate costs for this PR "
                                  "(no billable resources found, or the run failed). See the workflow logs.", encoding="utf-8")
        print("head.json missing or empty", file=sys.stderr)
        return 0  # should not block PRs because of tooling problems

    comment, delta = build_comment(load(args.base), head)
    Path(args.out).write_text(comment, encoding="utf-8")
    print(comment)

    if args.fail_over is not None and delta > args.fail_over:
        print(f"\nFAIL: burn increase {usd(delta)}/month exceeds limit {usd(args.fail_over)}/month", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())