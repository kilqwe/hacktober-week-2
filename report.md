## AWS Activate Credit Burner Report

**Starting credits:** $5,000.00
**Monthly burn (credit-covered):** $243.88/mo
### Estimated runway: 20.5 months (runs out ~2028-07-01)

> **Warning:** 1 item(s) could not be priced and are **not** in the total, so the real runway is **shorter** than shown.

---

### Line Items

| Resource | Type | Qty | Unit $/mo | Total $/mo | Share | Price source |
|---|---|---:|---:|---:|---:|---|
| aws_db_instance.primary_db | db.r5.large | 1 | $182.50 | $182.50 | 75% | cache |
| aws_instance.backend_api | t3.large | 1 | $60.74 | $60.74 | 25% | cache |
| aws_instance.backend_api (root volume) | 8 GB gp3 | 1 | $0.64 | $0.64 | 0% | estimate |
| aws_db_instance.primary_db (storage) | size not set | 1 | ? | ? | - | unknown (size unknown or from a variable - not priced) |

### Warnings
- Unpriced: aws_db_instance.primary_db (storage) (size unknown or from a variable - not priced)

---

### FinOps Advisor Tip (AI: qwen2.5:3b)
> Consider scaling down your RDS instance to a smaller size, such as db.r5.xsmall, to reduce costs while maintaining your application's current performance.

*All dollar figures above are computed by code from live pricing or labelled estimates; the AI model (qwen2.5:3b) only writes the tip.*
