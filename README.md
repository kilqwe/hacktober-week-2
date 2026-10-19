# Cred_Pred (A Credit Burn Analyzer)

Know when your cloud credits will run out before they do.

cred_pred reads your Terraform (or a plain-English description), prices the resources live through the Infracost API, and tells you your monthly burn, your runway in months, the date the credits run out, and how many credits will expire unused. A local open model (Ollama) writes a one-line cost-saving tip. A GitHub Action runs it on every pull request and comments with how the PR changes your burn and runway.

Built for startups and solo developers with cloud credits (for example AWS Activate) who never track the burn. Hacktoberfest Week 2, theme: Side Hustle.

## Quick start

```powershell
git clone <this repo>
cd <this repo>
pip install -r requirements.txt

# a free key from https://www.infracost.io
$env:INFRACOST_API_KEY = "your-key"      # or put it in a .env file

# local open model
ollama pull gemma2:2b

python cred_pred.py --credits 5000 --file infra\main.tf
```

The report is printed to the terminal and saved to `report.md`.

## Input options

| Input | Example |
|---|---|
| One Terraform file | `--file infra\main.tf` |
| A folder of `.tf` files | `--file path\to\folder` |
| Terraform plan JSON (recommended) | `--file plan.json` |
| Plain English (needs Ollama) | `--describe "2 t3.medium, a db.t3.small postgres, 1 NAT gateway"` |

For exact counts, use plan JSON. The `.tf` parser cannot expand `module` blocks, `for_each` or `dynamic` blocks, and warns when it meets them. To make plan JSON:

```powershell
terraform plan -out plan.out
terraform show -json plan.out | Out-File -Encoding utf8 plan.json
```

Useful options:

| Flag | What it does |
|---|---|
| `--expires 2027-06-30` | Reports credits that will go unused and the monthly spend needed to use them all |
| `--exclude rds,ebs` | Resource kinds that credits do not cover |
| `--model qwen2.5:3b` | Use a different Ollama model (default `gemma2:2b`) |
| `--no-llm` | Skip the advisor tip and use the rule-based one |
| `--no-cache` | Ignore the 7-day price cache |
| `--json-out out.json` | Write machine-readable JSON (use this instead of a shell redirect on Windows) |

## How it works

The model never does arithmetic and never prices anything. Every dollar figure comes from Infracost live pricing, the local cache, or a clearly labelled flat estimate. The model has two jobs only:

1. **Turn plain English into a resource inventory.** Python validates every entry (allowed kinds, instance-type pattern, sane quantity) before trusting it.
2. **Write the advisor tip.** A guard rejects the tip if it contains a dollar or percentage figure that was not in the facts given to the model, or names an instance type that is not in your inventory. The model gets one retry, told what was wrong. If that fails too, the tool falls back to a rule-based tip.

Other honesty features:

- A lookup that fails is shown as "unpriced", never as $0.
- If resource types are skipped, the report says the runway is an upper bound and names the types.
- A resource defined twice (for example in two `.tf` files) is detected and flagged, not silently double counted.
- Runs are repeatable: temperature 0 and a fixed seed.

## Why open models

- **Privacy.** Your infrastructure files and account details are processed on your machine or CI runner. Nothing is sent to a hosted model.
- **Swap models freely.** One flag (`--model`) or one GitHub variable (`CRED_MODEL`) changes the model. The bake-off below shows why that matters.
- **Zero per-call cost.** Inference is free, so it can run on every pull request.

## Model bake-off

`bench.py` runs each model on the two jobs above: 8 plain-English descriptions with known answers, and 3 advisor tips. Prices are fixed so every model sees identical facts. Run it yourself:

```powershell
python bench.py --models gemma2:2b,qwen2.5:3b,gemma3:4b,llama3.2:3b
```

| Model | Inventories exactly right | Item F1 | Extraction errors | Avg extract time | Tip passed 1st try | Tip needed retry | Tip fell back to rules | Avg tip time |
|---|---|---|---|---|---|---|---|---|
| gemma3:4b | 5/8 | 91% | 0 failed, 3 dropped | 3.5s | 3/3 | 0/3 | 0/3 | 3.0s |
| qwen2.5:3b | 8/8 | 100% | 0 failed, 0 dropped | 2.9s | 2/3 | 0/3 | 1/3 | 3.6s |
| gemma2:2b | TODO | | | | | | | |
| llama3.2:3b | TODO | | | | | | | |

What the bake-off showed:

- **Same numbers, different tips.** Costs came out identical to the cent whichever model was used. Only the tips varied, because the model never touches the math.
- **The guard caught real inventions.** Models made up instance sizes that do not exist (such as `db.r5.xsmall`) and unsupported savings claims, and the guard rejected them.
- **The guard does not catch bad advice.** One tip told the user to upgrade to a more powerful instance type in order to cut costs. It contained no invented figure or name, so it passed. Guards check facts, not judgment, so treat tips as hints.
- **Bigger is not automatically better.** The 4B model was fluent and sometimes wrong, where smaller models were vaguer but safer. For a two-sentence task, a laptop-sized model is enough, and a 70B model would break the "runs on a CI runner" story.

## GitHub Action

The workflow in `.github/workflows/` runs on every pull request that touches your Terraform. It estimates the base branch and the PR branch, then posts (and updates) one comment showing the change in burn and runway, plus the per-resource changes.

Setup:

1. Add a repository **secret**: `INFRACOST_API_KEY`.
2. Add repository **variables**:
   - `CREDITS`: remaining credit balance in USD (for example `5000`)
   - `CREDITS_EXPIRES`: expiry date, `YYYY-MM-DD`
   - `CRED_USE_LLM`: `true` to include the advisor tip
   - `CRED_MODEL`: Ollama model to use (for example `gemma3:4b`)
   - `CRED_MAX_INCREASE` (optional): fail the check if a PR adds more than this many USD per month
3. Make sure the workflow's `TF_DIR` and its path filter point at your Terraform folder.

Example comment from a PR that moved a `t3.large` to a `t3.xlarge` and added a load balancer: burn $279.03 to $356.19 (+$77.16/mo), runway 17.9 to 14.0 months.

Note: a PR check uses the workflow file and scripts from the PR branch, not from `main`. If you fix the workflow or scripts, push a new commit to the PR branch. "Re-run jobs" reuses the old commit and will not pick up the fix.

## Known limitations

- **Live prices:** EC2 and RDS instances. **Flat estimates (us-east-1 rates, labelled "estimate" in the report):** EBS, RDS storage, NAT gateway, load balancer, Elastic IP, EKS control plane, ECS Fargate.
- Fargate Spot is priced at the on-demand rate, so it is an upper bound.
- **Usage-based costs are not included:** data transfer, NAT per-GB charges, load balancer capacity units, CloudFront and S3 traffic, logs. Terraform cannot see them, so real burn is usually higher.
- **Not priced:** Lambda, S3, CloudFront, ECR, ElastiCache, and any resource type not listed above. They are named in the report and the PR comment.
- The `.tf` parser cannot expand modules, `for_each` or `dynamic` blocks. Use plan JSON for exact results.
- The credit balance is a static setting, not live spend. Update it by hand.
- Savings Plans, Reserved Instances, discounts, taxes and support plans are not modeled.
- EC2 is Linux on-demand only. RDS engines supported: Postgres, MySQL, MariaDB, Aurora (Postgres and MySQL).

## Tested on a real stack

A friend's Terraform project (11 files: ECS Fargate service, ALB, one EC2 instance, RDS Postgres, CloudFront and S3) was priced at about $81.37 per month, with CloudFront, S3 and ECR named as not priced. Running it on this stack also exposed the bugs that shaped the tool: a first version that returned "No billable resources found", a duplicated file that doubled the burn, and a misleading "304 months" runway. All three are fixed.

## What is next

- Cost Explorer integration, to compare predicted against actual spend.
- Live Infracost pricing for the flat-estimate items.
- A grounded tip prompt: hand the model vetted advice for the top cost item and let it tailor it.
- Price-check any instance type a tip names.
- Golden tests: hand-priced sample stacks checked against the AWS Pricing Calculator, plus a coverage score (share of cost priced live, estimated or unpriced).
- Package the Action so other repos can use it with one `uses:` line, and run it from the base branch for security.
