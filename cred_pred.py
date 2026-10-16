#!/usr/bin/env python3
"""
cred_pred.py - predict how long your cloud credits will last.

The LLM never does arithmetic or pricing:
  * .tf / terraform plan .json are parsed by code; plain English goes through a local
    Ollama model whose output is validated in Python.
  * Dollar figures come from Infracost live pricing or labelled flat estimates.
  * The LLM only writes the advisor tip, which is rejected if it invents numbers.

  python cred_pred.py --credits 5000 --file main.tf --expires 2027-06-30
  python cred_pred.py --credits 5000 --file plan.json --model qwen2.5:3b
  python cred_pred.py --credits 5000 --describe "2 t3.medium, db.t3.small postgres, 1 NAT gateway"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

HOURS_PER_MONTH = 730
DAYS_PER_MONTH = 30.44
INFRACOST_URL = "https://pricing.api.infracost.io/graphql"
CACHE_TTL = 7 * 24 * 3600

# Flat monthly estimates at us-east-1 rates (labelled "estimate" in the report)
FLAT = {
    "nat_gateway": (0.045 * HOURS_PER_MONTH, "hourly only; $0.045/GB data processing not included"),
    "load_balancer": (0.0225 * HOURS_PER_MONTH, "hourly only; LCU usage not included"),
    "elastic_ip": (0.005 * HOURS_PER_MONTH, "public IPv4 hourly charge"),
    "eks_cluster": (0.10 * HOURS_PER_MONTH, "control plane only; worker nodes priced separately"),
}
EBS_PER_GB = {"gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015, "standard": 0.05}
RDS_PER_GB = 0.115  # gp3 single-AZ; doubled for multi-AZ
RDS_ENGINES = {"postgres": "PostgreSQL", "postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB",
               "aurora-postgresql": "Aurora PostgreSQL", "aurora-mysql": "Aurora MySQL", "aurora": "Aurora MySQL"}
KINDS = ["ec2", "rds", "ebs", "nat_gateway", "load_balancer", "elastic_ip", "eks_cluster"]
INSTANCE_RE = re.compile(r"^[a-z][a-z0-9]*\d[a-z0-9-]*\.[a-z0-9]+$")
# Terraform types that cost ~nothing. Other unrecognised aws_* types are reported as "not priced".
FREE = ("aws_vpc", "aws_subnet", "aws_security_group", "aws_route", "aws_internet_gateway", "aws_iam",
        "aws_key_pair", "aws_network_acl", "aws_lb_listener", "aws_lb_target_group", "aws_alb_",
        "aws_db_subnet_group", "aws_db_parameter_group", "aws_eip_association", "aws_launch_template",
        "aws_cloudwatch_log_group", "aws_ssm_parameter", "aws_default_", "aws_volume_attachment",
        "aws_ebs_volume_attachment", "aws_main_route_table_association")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)  # stderr keeps --json output on stdout clean


class FatalError(Exception):
    pass


class LLMError(Exception):
    pass


@dataclass
class Resource:
    """One billable line item."""
    kind: str
    name: str
    region: str
    quantity: int = 1
    instance_type: str | None = None
    engine: str | None = None
    multi_az: bool = False
    size_gb: int = 0
    volume_type: str = "gp3"
    unit_monthly: float | None = None  # per unit; None = unknown
    price_source: str = "unknown"  # live | cache | estimate | unknown
    note: str = ""

    @property
    def monthly(self) -> float | None:
        return None if self.unit_monthly is None else self.unit_monthly * self.quantity

    @property
    def display_type(self) -> str:
        if self.kind in ("ebs", "rds_storage"):
            if self.size_gb <= 0:
                return "size not set"
            return f"{self.size_gb} GB" + (f" {self.volume_type}" if self.kind == "ebs" else "")
        return self.instance_type or self.kind

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "type": self.display_type, "region": self.region,
                "quantity": self.quantity, "unit_monthly": self.unit_monthly, "monthly": self.monthly,
                "price_source": self.price_source, "note": self.note}


# ----------------------------------------------------------------------------
# Value helpers. python-hcl2 keeps literal quotes on strings: '"t3.medium"'
# ----------------------------------------------------------------------------
def clean(v):
    if isinstance(v, str):
        v = v.strip()
        if len(v) > 1 and v[0] == v[-1] == '"':
            v = v[1:-1]
    return v


def resolve(v, variables: dict):
    """Literal or simple var.x reference -> value; anything else -> None."""
    v = clean(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\$\{\s*var\.(\w+)\s*\}|var\.(\w+)", v)
        if m:
            name = m.group(1) or m.group(2)
            return resolve(variables[name], {}) if name in variables else None
        if "${" in v or v.startswith(("var.", "local.", "data.", "module.")):
            return None
    return v


def to_int(v, default=None):
    try:
        return int(float(clean(v)))
    except (TypeError, ValueError):
        return default


def first_block(v) -> dict:
    v = v[0] if isinstance(v, list) and v else v
    return v if isinstance(v, dict) else {}


# ----------------------------------------------------------------------------
# Deterministic extraction (shared by .tf and plan JSON)
# ----------------------------------------------------------------------------
def components_for(rtype, name, cfg, region, qty, variables, warnings, skipped, addr=None) -> list[Resource]:
    """Turn one Terraform resource into priceable items (e.g. aws_instance -> EC2 + root volume)."""
    addr = addr or f"{rtype}.{name}"

    def get(key, default=None):
        v = resolve(cfg.get(key), variables)
        return default if v is None else v

    def res(kind, label=None, **kw):
        return Resource(kind, label or addr, region, qty, **kw)

    if rtype == "aws_instance":
        itype = get("instance_type")
        if not itype:
            warnings.append(f"{addr}: instance_type not resolvable - skipped")
            return []
        root = first_block(cfg.get("root_block_device"))
        return [res("ec2", instance_type=str(itype)),
                res("ebs", f"{addr} (root volume)", size_gb=to_int(resolve(root.get("volume_size"), variables), 8),
                    volume_type=str(resolve(root.get("volume_type"), variables) or "gp3"))]

    if rtype in ("aws_db_instance", "aws_rds_cluster_instance"):
        itype = get("instance_class")
        if not itype:
            warnings.append(f"{addr}: instance_class not resolvable - skipped")
            return []
        is_cluster = rtype == "aws_rds_cluster_instance"
        engine = get("engine", "aurora-postgresql" if is_cluster else None)
        if engine is None:
            warnings.append(f"{addr}: engine not set/resolvable, assuming postgres")
            engine = "postgres"
        multi = str(get("multi_az", False)).lower() == "true" and not is_cluster
        out = [res("rds", instance_type=str(itype), engine=str(engine).lower(), multi_az=multi)]
        if is_cluster:
            warnings.append(f"{addr}: Aurora storage/I-O is usage-based and not included")
        else:
            out.append(res("rds_storage", f"{addr} (storage)", size_gb=to_int(get("allocated_storage"), 0),
                           multi_az=multi))
        return out

    if rtype == "aws_ebs_volume":
        return [res("ebs", size_gb=to_int(get("size"), 0), volume_type=str(get("type", "gp3")))]
    simple = {"aws_nat_gateway": "nat_gateway", "aws_lb": "load_balancer", "aws_alb": "load_balancer",
              "aws_eip": "elastic_ip", "aws_eks_cluster": "eks_cluster"}
    if rtype in simple:
        return [res(simple[rtype])]
    if rtype == "aws_rds_cluster":
        return []  # priced via its aws_rds_cluster_instance children
    if rtype.startswith("aws_") and not rtype.startswith(FREE):
        skipped.add(rtype)
    return []


def extract_from_hcl(text, default_region, warnings, skipped) -> list[Resource]:
    try:
        import hcl2
        parsed = hcl2.loads(text)
    except ImportError as e:
        raise FatalError("python-hcl2 is required for .tf files: pip install python-hcl2") from e
    except Exception as e:
        first_line = (str(e).strip().splitlines() or ["unknown error"])[0][:150]
        raise FatalError(f"Could not parse this Terraform file ({first_line}). The .tf parser does not support "
                         f"every Terraform feature. Run `terraform show -json plan.out > plan.json` and use "
                         f"--file plan.json instead.") from e

    variables = {clean(n): c["default"] for b in parsed.get("variable", []) for n, c in b.items()
                 if isinstance(c, dict) and "default" in c}
    region = default_region
    for block in parsed.get("provider", []):
        for pname, pcfg in block.items():
            if clean(pname) == "aws" and isinstance(pcfg, dict) and "region" in pcfg:
                r = resolve(pcfg["region"], variables)
                if isinstance(r, str):
                    region = r
                else:
                    warnings.append(f"AWS provider region not resolvable; using {default_region}")

    if parsed.get("module"):
        warnings.append(f"{len(parsed['module'])} module block(s) are not expanded, so resources inside them are "
                        f"NOT counted. Use plan JSON (`terraform show -json`) for exact results.")
    out: list[Resource] = []
    for block in parsed.get("resource", []):
        for rtype, instances in block.items():
            for name, cfg in instances.items():
                rtype, name = clean(rtype), clean(name)
                if not isinstance(cfg, dict):
                    continue
                if "for_each" in cfg:
                    warnings.append(f"{rtype}.{name}: for_each not expanded, counted once "
                                    f"(use `terraform show -json` plan output for exact counts)")
                if "dynamic" in cfg:
                    warnings.append(f"{rtype}.{name}: dynamic blocks are not expanded; costs may be understated")
                if "provider" in cfg:
                    warnings.append(f"{rtype}.{name}: provider alias ignored, using {region}")
                qty = 1
                if "count" in cfg:
                    qty = to_int(resolve(cfg["count"], variables))
                    if qty is None:
                        warnings.append(f"{rtype}.{name}: count not resolvable, assuming 1")
                        qty = 1
                out += components_for(rtype, name, cfg, region, qty, variables, warnings, skipped)
    return out


def extract_from_plan(text, default_region, warnings, skipped) -> list[Resource]:
    """`terraform show -json plan.out`: variables, modules, count and for_each are already expanded."""
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as e:
        raise FatalError(f"Invalid JSON: {e}") from e
    if "resource_changes" not in plan:
        raise FatalError("JSON is not a terraform plan (no resource_changes). Use `terraform show -json`.")
    try:
        region = plan["configuration"]["provider_config"]["aws"]["expressions"]["region"]["constant_value"]
    except (KeyError, TypeError):
        region = default_region

    out: list[Resource] = []
    for rc in plan["resource_changes"]:
        change = rc.get("change", {})
        if change.get("actions") == ["delete"] or not isinstance(change.get("after"), dict):
            continue
        out += components_for(rc.get("type", ""), rc.get("name", ""), change["after"], region, 1, {},
                              warnings, skipped, addr=rc.get("address"))
    return out


# ----------------------------------------------------------------------------
# Local LLM (Ollama): plain-English extraction and the advisor tip
# ----------------------------------------------------------------------------
def ollama_generate(base_url, model, prompt, schema=None, timeout=180) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0, "seed": 42}}
    if schema:
        payload["format"] = schema
    try:
        r = requests.post(f"{base_url.rstrip('/')}/api/generate", json=payload, timeout=timeout)
    except requests.ConnectionError as e:
        raise LLMError(f"Cannot reach Ollama at {base_url}. Is `ollama serve` running?") from e
    except requests.Timeout as e:
        raise LLMError(f"Ollama timed out after {timeout}s") from e
    if r.status_code == 404:
        raise LLMError(f"Model '{model}' not found. Run: ollama pull {model}")
    if not r.ok:
        raise LLMError(f"Ollama error {r.status_code}: {r.text[:200]}")
    return r.json().get("response", "")


INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {"resources": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": KINDS}, "instance_type": {"type": "string"},
                       "quantity": {"type": "integer"}, "engine": {"type": "string"},
                       "multi_az": {"type": "boolean"}, "size_gb": {"type": "integer"}},
        "required": ["kind", "quantity"]}}},
    "required": ["resources"],
}
_EX_IN = "3 m5.large servers, a multi-AZ mysql db.r5.large, two 100GB gp3 volumes, one load balancer"
_EX_OUT = {"resources": [
    {"kind": "ec2", "instance_type": "m5.large", "quantity": 3},
    {"kind": "rds", "instance_type": "db.r5.large", "engine": "mysql", "multi_az": True, "quantity": 1},
    {"kind": "ebs", "size_gb": 100, "quantity": 2},
    {"kind": "load_balancer", "quantity": 1}]}


def extract_from_text(text, region, base_url, model, warnings) -> list[Resource]:
    """The model proposes an inventory; Python validates every entry before it is trusted."""
    prompt = (
        "Convert a plain-English description of AWS infrastructure into JSON.\n"
        "Rules: one entry per group of identical resources; 'quantity' is the number of copies. "
        "kind: ec2 = server/VM, rds = database (engine: postgres, mysql, mariadb, aurora-postgresql, "
        "aurora-mysql), ebs = extra disk (size_gb), nat_gateway, load_balancer, elastic_ip, eks_cluster. "
        "Only include what is explicitly mentioned. Never compute prices.\n\n"
        f"Input: {_EX_IN}\nOutput: {json.dumps(_EX_OUT)}\n\nInput: {text}\nOutput:")
    log(f"Extracting inventory with local model '{model}'...")
    raw = ollama_generate(base_url, model, prompt, schema=INVENTORY_SCHEMA)
    try:
        items = json.loads(raw)["resources"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise LLMError(f"Model returned unusable output: {raw[:200]!r}") from e

    out: list[Resource] = []
    for i, it in enumerate(items, 1):
        label, kind, qty = f"described[{i}]", it.get("kind"), to_int(it.get("quantity"), 1)
        itype = (it.get("instance_type") or "").strip().lower() or None
        engine = (it.get("engine") or "").strip().lower() or None
        size = to_int(it.get("size_gb"), 0)
        if kind not in KINDS or not 1 <= qty <= 500:
            warnings.append(f"{label}: dropped invalid entry from model: {it}")
            continue
        if kind == "rds":
            if itype and not itype.startswith("db."):
                warnings.append(f"{label}: prefixed '{itype}' with 'db.' for RDS")
                itype = "db." + itype
            if not engine:
                warnings.append(f"{label}: no database engine given, assuming postgres")
                engine = "postgres"
        if kind in ("ec2", "rds") and not (itype and INSTANCE_RE.match(itype.removeprefix("db."))):
            warnings.append(f"{label}: dropped {kind} with invalid instance type {itype!r}")
            continue
        if kind == "ebs" and size <= 0:
            warnings.append(f"{label}: dropped EBS volume with no size")
            continue
        out.append(Resource(kind, label, region, qty,
                            instance_type=itype if kind in ("ec2", "rds") else None,
                            engine=engine if kind == "rds" else None,
                            multi_az=bool(it.get("multi_az")) if kind == "rds" else False,
                            size_gb=size if kind == "ebs" else 0))
    return out


# ----------------------------------------------------------------------------
# Pricing: Infracost live for EC2/RDS, labelled flat estimates for the rest
# ----------------------------------------------------------------------------
class Pricer:
    def __init__(self, api_key: str | None, cache_path: Path | None):
        self.api_key, self.cache_path, self.cache = api_key, cache_path, {}
        if cache_path and cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    def price(self, r: Resource) -> None:
        """Fill r.unit_monthly / price_source / note in place."""
        self._live(r) if r.kind in ("ec2", "rds") else self._estimate(r)

    def _estimate(self, r: Resource) -> None:
        if r.kind in FLAT:
            unit, note = FLAT[r.kind]
        elif r.size_gb <= 0:
            r.note = "size unknown or from a variable - not priced"
            return
        elif r.kind == "ebs":
            rate = EBS_PER_GB.get(r.volume_type)
            note = "" if rate else f"unknown volume type '{r.volume_type}', priced as gp3"
            unit = r.size_gb * (rate or EBS_PER_GB["gp3"])
        else:  # rds_storage
            unit = r.size_gb * RDS_PER_GB * (2 if r.multi_az else 1)
            note = "gp3 rate" + (", doubled for multi-AZ" if r.multi_az else "")
        if r.region != "us-east-1":
            note = (note + "; " if note else "") + "us-east-1 rates, other regions differ"
        r.unit_monthly, r.price_source, r.note = round(unit, 2), "estimate", note

    def _live(self, r: Resource) -> None:
        if r.kind == "rds" and r.engine not in RDS_ENGINES:
            r.note = f"unsupported database engine '{r.engine}'"
            return
        key = f"{r.kind}|{r.region}|{r.instance_type}|{r.engine}|{r.multi_az}"
        hit = self.cache.get(key)
        if hit and time.time() - hit["ts"] < CACHE_TTL:
            r.unit_monthly, r.price_source = round(hit["hourly"] * HOURS_PER_MONTH, 2), "cache"
            return
        if not self.api_key:
            r.note = "no INFRACOST_API_KEY"
            return
        log(f"Fetching live price: {r.instance_type} in {r.region} ({r.kind})...")
        try:
            hourly = self._query(r)
        except FatalError:
            raise
        except Exception as e:
            r.note = str(e)
            log(f"  ! {r.name}: {e}")
            return
        self.cache[key] = {"hourly": hourly, "ts": time.time()}
        if self.cache_path:
            try:
                self.cache_path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
            except OSError:
                pass
        r.unit_monthly, r.price_source = round(hourly * HOURS_PER_MONTH, 2), "live"

    def _query(self, r: Resource) -> float:
        if r.kind == "ec2":
            service, family = "AmazonEC2", "Compute Instance"
            attrs = [("instanceType", r.instance_type), ("operatingSystem", "Linux"), ("tenancy", "Shared"),
                     ("capacitystatus", "Used"), ("preInstalledSw", "NA")]
        else:
            service, family = "AmazonRDS", "Database Instance"
            attrs = [("instanceType", r.instance_type), ("databaseEngine", RDS_ENGINES[r.engine]),
                     ("deploymentOption", "Multi-AZ" if r.multi_az else "Single-AZ")]
        attr_gql = ", ".join(f"{{key: {json.dumps(k)}, value: {json.dumps(v)}}}" for k, v in attrs)
        query = f"""
        query($vendor: String!, $service: String!, $family: String!, $region: String!) {{
          products(filter: {{vendorName: $vendor, service: $service, productFamily: $family,
                            region: $region, attributeFilters: [{attr_gql}]}}) {{
            prices(filter: {{purchaseOption: "on_demand"}}) {{ USD }}
          }}
        }}"""
        variables = {"vendor": "aws", "service": service, "family": family, "region": r.region}
        try:
            resp = requests.post(INFRACOST_URL, json={"query": query, "variables": variables}, timeout=30,
                                 headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"})
        except requests.RequestException as e:
            raise RuntimeError(f"network error: {e}") from e
        if resp.status_code in (401, 403):
            raise FatalError("Infracost rejected the API key (401/403). Check INFRACOST_API_KEY.")
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(f"non-JSON response (HTTP {resp.status_code})") from e
        if data.get("errors"):
            raise RuntimeError(f"GraphQL error: {data['errors'][0].get('message')}")
        for product in (data.get("data") or {}).get("products") or []:
            for p in product.get("prices") or []:
                try:
                    if float(p.get("USD")) > 0:
                        return float(p["USD"])
                except (TypeError, ValueError):
                    continue
        raise RuntimeError("no on-demand price found (check instance type / region / engine)")


# ----------------------------------------------------------------------------
# Credit math: pure Python
# ----------------------------------------------------------------------------
def analyze_credits(credits: float, monthly: float, expires: date | None, today: date) -> dict:
    out = {"credits": credits, "monthly_covered": monthly, "expires": expires.isoformat() if expires else None,
           "runway_months": None, "runs_out": None, "months_to_expiry": None, "status": "no_cost",
           "wasted": 0.0, "needed_monthly_to_use_all": None}
    months_left = (expires - today).days / DAYS_PER_MONTH if expires else None
    if months_left is not None:
        out["months_to_expiry"] = round(months_left, 1)
    if monthly <= 0:
        return out
    runway = credits / monthly
    out["runway_months"] = round(runway, 1)
    out["runs_out"] = (today + timedelta(days=runway * DAYS_PER_MONTH)).isoformat()
    out["status"] = "runs_out_first"
    if months_left is not None:
        if months_left <= 0:
            out["status"], out["wasted"] = "expired", round(credits, 2)
        elif runway > months_left:
            out.update(status="expires_first", wasted=round(credits - monthly * months_left, 2),
                       needed_monthly_to_use_all=round(credits / months_left, 2))
    return out


# ----------------------------------------------------------------------------
# Advisor: LLM tip guarded against invented numbers, with a rule-based fallback
# ----------------------------------------------------------------------------
TIPS = {
    "nat_gateway": "NAT gateways bill hourly plus per GB. For non-prod use one NAT instead of one per AZ, and add free S3/DynamoDB gateway VPC endpoints so that traffic skips the NAT.",
    "rds": "Your database is the top cost. Turn off Multi-AZ outside production, right-size the instance class, and stop dev databases overnight.",
    "ec2": "Your compute is the top cost. Try Graviton (t4g/m7g) instances, shut down non-prod instances outside working hours, and right-size using CloudWatch CPU data.",
    "load_balancer": "Load balancers bill 24/7. Consolidate services behind one ALB using host/path rules.",
    "eks_cluster": "The EKS control plane costs the same when idle. Share one cluster across environments using namespaces.",
    "ebs": "Volumes bill whether or not they are attached. Delete orphaned volumes and move gp2 to gp3.",
    "elastic_ip": "Public IPv4 addresses are billed hourly. Release unused Elastic IPs.",
}


def fallback_tip(items: list[Resource]) -> str:
    return TIPS.get(items[0].kind, "Review your largest line item for right-sizing opportunities.") if items \
        else "No priced resources, so no tip."


_FIGURE = re.compile(r"\$\s*(\d[\d,]*\.?\d*)|(\d[\d,]*\.?\d*)\s*(?:%|percent|dollars|usd|/mo\b|per month|a month)", re.I)


def _norm(m: str) -> str:
    return m.rstrip(".,").replace(",", "")


def _figures(tip: str) -> set[str]:
    """Money and percentage claims in the model's tip. Names like t4g/gp3/S3 and plain counts are ignored."""
    return {_norm(a or b) for a, b in _FIGURE.findall(tip)}


_INSTANCE_MENTION = re.compile(r"\b(?:db\.)?[a-z]{1,3}\d[a-z0-9-]*\.(?:nano|micro|small|medium|large|\d*xlarge|metal)\b", re.I)


def _unknown_instance_types(tip: str, items) -> set[str]:
    """Instance types the tip names that are not in the user's inventory (e.g. an invented 'r5.medium')."""
    known = set()
    for r in items:
        if r.instance_type:
            t = r.instance_type.lower()
            known |= {t, t.removeprefix("db.")}
    return {m.lower() for m in _INSTANCE_MENTION.findall(tip)} - known


def advisor_tip(items, credit_info, base_url, model, use_llm, warnings) -> tuple[str, str]:
    """Returns (tip, source) where source is 'llm:<model>' or 'rules'."""
    if not items or not use_llm:
        return fallback_tip(items), "rules"
    total = sum(r.monthly for r in items)
    facts = "Largest cost items:\n" + "\n".join(
        f"{i}. {r.kind} {r.display_type} x{r.quantity}: {r.monthly / total * 100:.0f}% of the bill"
        for i, r in enumerate(items[:5], 1))
    if credit_info["status"] == "expires_first":
        facts += "\nThe credits will expire before they are used up."
    elif credit_info["status"] == "runs_out_first":
        facts += "\nThe credits will run out before they expire."
    allowed = {_norm(m) for m in re.findall(r"\d[\d,]*\.?\d*", facts)}
    head = (f"You are a cloud cost advisor for a small startup.\n{facts}\n\n"
            "Give ONE specific, actionable tip (max 2 sentences) that targets the largest item. "
            "Do not write any dollar amounts or percentages that are not listed above, and do not "
            "name any instance type that is not listed above.")

    prompt, invented = head + "\n\nTip:", set()
    for _ in range(2):  # one retry: weak models often fix this when told what went wrong
        try:
            tip = ollama_generate(base_url, model, prompt, timeout=120).strip()
        except LLMError as e:
            warnings.append(f"Advisor LLM unavailable ({e}); used rule-based tip.")
            return fallback_tip(items), "rules"
        invented = _figures(tip) - allowed
        bad_types = _unknown_instance_types(tip, items)
        if tip and not invented and not bad_types:
            return tip, f"llm:{model}"
        problems = []
        if invented:
            problems.append(f"figures that were not provided ({', '.join(sorted(invented))})")
        if bad_types:
            problems.append(f"instance types that are not in the inventory ({', '.join(sorted(bad_types))})")
        problem = "contained " + " and ".join(problems) if problems else "was empty"
        prompt = head + (f"\n\nYour previous answer {problem}. Rewrite it with no dollar amounts, "
                         f"percentages, or instance types other than the listed ones.\n\nTip:")
    flagged = sorted(invented | bad_types)
    warnings.append(f"Advisor LLM tip rejected twice (unverified: {flagged or 'empty answer'}); used rule-based tip.")
    return fallback_tip(items), "rules"


# ----------------------------------------------------------------------------
# Report + CLI
# ----------------------------------------------------------------------------
def money(x) -> str:
    return "?" if x is None else f"${x:,.2f}"


def generate_report(c, items, unknown, excluded, oop, total_all, tip, tip_src, warnings, skipped, model) -> str:
    L = ["## AWS Activate Credit Burner Report", "",
         f"**Starting credits:** {money(c['credits'])}",
         f"**Monthly burn (credit-covered):** {money(c['monthly_covered'])}/mo"]
    if oop > 0:
        L.append(f"**Out-of-pocket (excluded from credits):** {money(oop)}/mo")
    L.append("### Estimated runway: unknown (no priced resources)" if c["runway_months"] is None else
             f"### Estimated runway: {c['runway_months']} months (runs out ~{c['runs_out']})")
    if c["expires"]:
        L.append(f"**Credits expire:** {c['expires']} ({c['months_to_expiry']} months from now)")
    L.append("")
    if c["status"] == "expired":
        L.append("> **Credits expired.** These credits have already expired.")
    elif c["status"] == "expires_first":
        L.append(f"> **Credits expire first.** About **{money(c['wasted'])}** will go unused at this burn rate. "
                 f"You would need to spend ~{money(c['needed_monthly_to_use_all'])}/mo to use them all before expiry.")
    elif c["status"] == "runs_out_first" and c["expires"]:
        L.append("> Credits will be used up before they expire.")
    if unknown:
        L.append(f"> **Warning:** {len(unknown)} item(s) could not be priced and are **not** in the total, "
                 f"so the real runway is **shorter** than shown.")
    L += ["", "---", "", "### Line Items", "",
          "| Resource | Type | Qty | Unit $/mo | Total $/mo | Share | Price source |",
          "|---|---|---:|---:|---:|---:|---|"]
    for r in items + unknown:
        share = f"{r.monthly / total_all * 100:.0f}%" if r.monthly is not None and total_all > 0 else "-"
        src = r.price_source if r.price_source != "unknown" else f"unknown ({r.note})" if r.note else "unknown"
        mark = " *(not credit-covered)*" if r.kind in excluded else ""
        L.append(f"| {r.name}{mark} | {r.display_type} | {r.quantity} | {money(r.unit_monthly)} | "
                 f"{money(r.monthly)} | {share} | {src} |")
    notes = sorted({f"{r.kind}: {r.note}" for r in items if r.price_source == "estimate" and r.note})
    if notes:
        L += ["", "*Estimate notes:*"] + [f"- {n}" for n in notes]
    if skipped:
        L += ["", f"**Not priced (unsupported types):** {', '.join(sorted(skipped))}"]
    if warnings:
        L += ["", "### Warnings"] + [f"- {w}" for w in warnings]
    who = f"AI: {tip_src[4:]}" if tip_src.startswith("llm:") else "rule-based"
    L += ["", "---", "", f"### FinOps Advisor Tip ({who})", f"> {tip}", "",
          f"*All dollar figures above are computed by code from live pricing or labelled estimates; "
          f"the AI model ({model}) only writes the tip.*", ""]
    return "\n".join(L)


def parse_args():
    p = argparse.ArgumentParser(description="Predict how long your cloud credits will last.")
    p.add_argument("--credits", type=float, required=True, help="Remaining credit balance in USD")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help=".tf (Terraform), .json (terraform show -json), or .txt (plain English)")
    src.add_argument("--describe", help='Plain English, e.g. "2 t3.medium and a db.t3.small postgres"')
    p.add_argument("--expires", help="Credit expiry date, YYYY-MM-DD")
    p.add_argument("--region", default="us-east-1", help="Default region when not found in the input")
    p.add_argument("--model", default="gemma2:2b", help="Ollama model (gemma2:2b, llama3.2:3b, qwen2.5:3b...)")
    p.add_argument("--ollama-url", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    p.add_argument("--exclude", default="", help="Comma-separated kinds NOT covered by credits (e.g. rds,ebs)")
    p.add_argument("--no-llm", action="store_true", help="Skip the advisor LLM (plain-English input still needs it)")
    p.add_argument("--no-cache", action="store_true", help="Ignore the price cache")
    p.add_argument("--cache-file", default=".price_cache.json")
    p.add_argument("--out", default="report.md", help="Markdown report path")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout (for CI)")
    return p.parse_args()


def run(args) -> int:
    load_dotenv()
    url = args.ollama_url if args.ollama_url.startswith("http") else "http://" + args.ollama_url
    if args.credits <= 0:
        raise FatalError("--credits must be greater than 0")
    expires = None
    if args.expires:
        try:
            expires = datetime.strptime(args.expires, "%Y-%m-%d").date()
        except ValueError as e:
            raise FatalError("--expires must look like 2027-06-30") from e

    warnings: list[str] = []
    skipped: set[str] = set()
    path = Path(args.file) if args.file else None
    suffix = path.suffix.lower() if path else ""
    try:
        if path and not path.exists():
            raise FatalError(f"File not found: {path}")
        if suffix == ".tf":
            resources = extract_from_hcl(path.read_text(encoding="utf-8"), args.region, warnings, skipped)
        elif suffix == ".json":
            resources = extract_from_plan(path.read_text(encoding="utf-8"), args.region, warnings, skipped)
        else:  # --describe, or a plain-text file
            text = args.describe or path.read_text(encoding="utf-8")
            resources = extract_from_text(text, args.region, url, args.model, warnings)
            warnings.append("Inventory was extracted by an AI model from plain English. Verify the line items.")
    except LLMError as e:
        raise FatalError(str(e)) from e
    if not resources:
        raise FatalError("No billable resources found in the input.")

    api_key = os.getenv("INFRACOST_API_KEY")
    if not api_key and any(r.kind in ("ec2", "rds") for r in resources):
        log("Warning: INFRACOST_API_KEY missing; EC2/RDS will only price from cache.")
    pricer = Pricer(api_key, None if args.no_cache else Path(args.cache_file))
    for r in resources:
        pricer.price(r)

    priced = sorted((r for r in resources if r.monthly is not None), key=lambda r: r.monthly, reverse=True)
    unknown = [r for r in resources if r.monthly is None]
    excluded = {k.strip() for k in args.exclude.split(",") if k.strip()}
    total_all = sum(r.monthly for r in priced)
    oop = sum(r.monthly for r in priced if r.kind in excluded)
    credit_info = analyze_credits(args.credits, total_all - oop, expires, date.today())
    if unknown:
        warnings.append("Unpriced: " + "; ".join(f"{r.name} ({r.note or 'unknown'})" for r in unknown))

    tip, tip_src = advisor_tip(priced, credit_info, url, args.model, not args.no_llm, warnings)
    report = generate_report(credit_info, priced, unknown, excluded, oop, total_all, tip, tip_src,
                             warnings, skipped, args.model)
    Path(args.out).write_text(report, encoding="utf-8")

    if args.json:
        print(json.dumps({"credits": credit_info, "total_monthly": round(total_all, 2),
                          "out_of_pocket_monthly": round(oop, 2),
                          "resources": [r.to_dict() for r in priced + unknown],
                          "unpriced_types": sorted(skipped), "warnings": warnings,
                          "tip": {"text": tip, "source": tip_src}}, indent=2))
    else:
        print(report)
        log(f"\nDone. Report saved to {args.out}.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr): 
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return run(parse_args())
    except FatalError as e:
        log(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())