## AWS Activate Credit Burner Report

**Starting credits:** $5,000.00
**Monthly burn (credit-covered):** $81.37/mo
### Estimated runway: 61.4 months (runs out ~2031-12-02)

> **Warning:** these resource types are not priced and are NOT in the total: aws_cloudfront_distribution, aws_ecr_lifecycle_policy, aws_ecr_repository, aws_s3_bucket. The real burn is higher, so this runway is an upper bound.

---

### Line Items

| Resource | Type | Qty | Unit $/mo | Total $/mo | Share | Price source |
|---|---|---:|---:|---:|---:|---|
| aws_ecs_service.app | 1 vCPU / 2 GB ARM64 | 1 | $28.84 | $28.84 | 35% | estimate |
| aws_lb.main | load_balancer | 1 | $16.43 | $16.43 | 20% | estimate |
| aws_instance.singleton | t4g.small | 1 | $12.26 | $12.26 | 15% | live |
| aws_db_instance.postgres | db.t4g.micro | 1 | $11.68 | $11.68 | 14% | live |
| aws_eip.singleton | elastic_ip | 1 | $3.65 | $3.65 | 4% | estimate |
| aws_ecs_service.app (task public IPv4) | elastic_ip | 1 | $3.65 | $3.65 | 4% | estimate |
| aws_instance.singleton (root volume) | 32 GB gp3 | 1 | $2.56 | $2.56 | 3% | estimate |
| aws_db_instance.postgres (storage) | 20 GB | 1 | $2.30 | $2.30 | 3% | estimate |

*Estimate notes:*
- elastic_ip: public IPv4 hourly charge
- fargate_task: on-demand Fargate rate, vCPU and memory only; this stack uses Spot, which is cheaper, so this is an upper bound
- load_balancer: hourly only; LCU usage not included
- rds_storage: gp3 rate

**Not priced (unsupported types):** aws_cloudfront_distribution, aws_ecr_lifecycle_policy, aws_ecr_repository, aws_s3_bucket

### Warnings
- aws_ecs_service.app: priced at 1 task(s); autoscaling can scale up to 2, and the cost scales with it

---

### FinOps Advisor Tip (AI: gemma3:4b)
> Focus on optimizing your Fargate tasks by right-sizing the vCPU allocation to the minimum needed for your application’s performance requirements. Regularly monitor your Fargate task resource utilization to identify and adjust any over-provisioning.

*All dollar figures above are computed by code from live pricing or labelled estimates; the AI model (gemma3:4b) only writes the tip.*
