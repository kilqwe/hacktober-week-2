"""
bench.py - compare local Ollama models on the two jobs the model does in cred_pred.py.

  run: python bench.py --models gemma3:4b,qwen2.5:3b,llama3.2:3b

Job 1: turn plain English into a resource inventory (scored against known answers).
Job 2: write the advisor tip (scored on whether it passes the guard that blocks invented figures).
Prices, totals and runway never involve the model, so they are not benchmarked.
"""
import argparse
import os
import sys
import time
from collections import Counter

import cred_pred as cp





# Test cases: (plain English, expected inventory as (kind, instance_type, quantity, engine, multi_az, size_gb))





CASES = [
    ("2 t3.medium servers and a db.t3.small postgres database",
     [("ec2", "t3.medium", 2, "", False, 0), ("rds", "db.t3.small", 1, "postgres", False, 0)]),
    ("one m5.large web server, a multi-AZ mysql db.r5.large database, and 1 NAT gateway",
     [("ec2", "m5.large", 1, "", False, 0), ("rds", "db.r5.large", 1, "mysql", True, 0), ("nat_gateway", "", 1, "", False, 0)]),
    ("3 t3.large servers behind a load balancer, plus a 200GB gp3 volume",
     [("ec2", "t3.large", 3, "", False, 0), ("load_balancer", "", 1, "", False, 0), ("ebs", "", 1, "", False, 200)]),
    ("a single c5.xlarge instance and an elastic IP",
     [("ec2", "c5.xlarge", 1, "", False, 0), ("elastic_ip", "", 1, "", False, 0)]),
    ("4 t3.small servers and 2 NAT gateways",
     [("ec2", "t3.small", 4, "", False, 0), ("nat_gateway", "", 2, "", False, 0)]),
    ("an EKS cluster with 3 m5.large nodes",
     [("eks_cluster", "", 1, "", False, 0), ("ec2", "m5.large", 3, "", False, 0)]),
    ("two mariadb databases on db.t3.medium and one t3.micro server",
     [("rds", "db.t3.medium", 2, "mariadb", False, 0), ("ec2", "t3.micro", 1, "", False, 0)]),
    ("one aurora-postgresql db.r5.large instance and 2 load balancers",
     [("rds", "db.r5.large", 1, "aurora-postgresql", False, 0), ("load_balancer", "", 2, "", False, 0)]),
]

TIP_CASES = [1, 2, 5]  # which inventories above get an advisor tip

PRICE = {"ec2": 60.74, "rds": 182.50, "ebs": 16.0, "nat_gateway": 32.85, "load_balancer": 16.43,
         "elastic_ip": 3.65, "eks_cluster": 73.0}  # fixed prices so every model sees identical facts





# Scoring





def norm(r):
    return (r.kind, r.instance_type or "", r.quantity, r.engine or "", bool(r.multi_az),
            r.size_gb if r.kind == "ebs" else 0)


def bench_extraction(model, url):
    s = {"exact": 0, "tp": 0, "got": 0, "exp": 0, "failed": 0, "dropped": 0, "secs": []}
    for text, expected in CASES:
        warnings = []
        t0 = time.perf_counter()
        try:
            res = cp.extract_from_text(text, "us-east-1", url, model, warnings)
        except cp.LLMError as e:
            if "not found" in str(e) or "Cannot reach" in str(e):
                raise  # model missing or Ollama down: stop, do not score it
            s["failed"] += 1
            s["exp"] += len(expected)
            continue
        s["secs"].append(time.perf_counter() - t0)
        got, exp = Counter(norm(r) for r in res), Counter(expected)
        s["tp"] += sum((got & exp).values())
        s["got"] += sum(got.values())
        s["exp"] += sum(exp.values())
        s["exact"] += got == exp
        s["dropped"] += sum("dropped" in w for w in warnings)
    return s


def build_items(expected):
    items = []
    for kind, itype, qty, engine, multi, size in expected:
        r = cp.Resource(kind, f"{kind}", "us-east-1", qty, instance_type=itype or None, engine=engine or None,
                        multi_az=multi, size_gb=size)
        r.unit_monthly, r.price_source = PRICE[kind], "estimate"
        items.append(r)
    return sorted(items, key=lambda r: r.monthly, reverse=True)


def bench_tips(model, url):
    calls, out, real = [], [], cp.ollama_generate

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    cp.ollama_generate = counting
    try:
        for i in TIP_CASES:
            calls.clear()
            t0 = time.perf_counter()
            tip, src = cp.advisor_tip(build_items(CASES[i][1]), {"status": "runs_out_first"}, url, model, True, [])
            out.append({"case": CASES[i][0], "tip": tip, "source": src, "calls": len(calls),
                        "secs": time.perf_counter() - t0})
    finally:
        cp.ollama_generate = real
    return out





# Report





def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def summarize(model, ex, tips):
    p, r = ex["tp"] / max(ex["got"], 1), ex["tp"] / max(ex["exp"], 1)
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    first = sum(t["source"].startswith("llm:") and t["calls"] == 1 for t in tips)
    retry = sum(t["source"].startswith("llm:") and t["calls"] > 1 for t in tips)
    fallback = sum(t["source"] == "rules" for t in tips)
    return (f"| {model} | {ex['exact']}/{len(CASES)} | {f1:.0%} | {ex['failed']} failed, {ex['dropped']} dropped | "
            f"{avg(ex['secs']):.1f}s | {first}/{len(tips)} | {retry}/{len(tips)} | {fallback}/{len(tips)} | "
            f"{avg([t['secs'] for t in tips]):.1f}s |")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Benchmark local Ollama models for cred_pred.py")
    ap.add_argument("--models", default="gemma3:4b,qwen2.5:3b,llama3.2:3b")
    ap.add_argument("--ollama-url", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--out", default="bench_results.md")
    args = ap.parse_args()
    url = args.ollama_url if args.ollama_url.startswith("http") else "http://" + args.ollama_url

    rows, samples = [], []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"\n=== {model} ===", file=sys.stderr)
        try:
            cp.ollama_generate(url, model, "Reply with OK.", timeout=300)  # warm-up so load time is not scored
            ex = bench_extraction(model, url)
            tips = bench_tips(model, url)
        except cp.LLMError as e:
            print(f"skipping {model}: {e}", file=sys.stderr)
            rows.append(f"| {model} | not available | | | | | | | |")
            continue
        rows.append(summarize(model, ex, tips))
        samples.append((model, tips))

    lines = ["# Model bake-off", "",
             f"{len(CASES)} plain-English descriptions with known answers, and {len(TIP_CASES)} advisor tips per model. "
             "Seed and temperature are fixed, so runs are repeatable.", "",
             "| Model | Inventories exactly right | Item F1 | Extraction errors | Avg extract time | Tip passed 1st try | Tip needed retry | Tip fell back to rules | Avg tip time |",
             "|---|---|---|---|---|---|---|---|---|"] + rows
    lines += ["", "## Tips written (rate each 1-5 for how useful and correct it is)", ""]
    for model, tips in samples:
        lines.append(f"### {model}")
        for t in tips:
            lines.append(f"- *{t['case']}* ({t['source']}, {t['calls']} call(s)): {t['tip']}  \n  Rating: __/5")
        lines.append("")
    report = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\nSaved to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()