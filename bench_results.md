# Model bake-off

8 plain-English descriptions with known answers, and 3 advisor tips per model. Seed and temperature are fixed, so runs are repeatable.

| Model | Inventories exactly right | Item F1 | Extraction errors | Avg extract time | Tip passed 1st try | Tip needed retry | Tip fell back to rules | Avg tip time |
|---|---|---|---|---|---|---|---|---|
| gemma3:4b | 5/8 | 91% | 0 failed, 3 dropped | 3.5s | 3/3 | 0/3 | 0/3 | 3.0s |
| qwen2.5:3b | 8/8 | 100% | 0 failed, 0 dropped | 2.9s | 2/3 | 0/3 | 1/3 | 3.6s |
| llama3.2:3b | not available | | | | | | | |

## Tips written (rate each 1-5 for how useful and correct it is)

### gemma3:4b
- *one m5.large web server, a multi-AZ mysql db.r5.large database, and 1 NAT gateway* (llm:gemma3:4b, 1 call(s)): Optimize your RDS db.r5.large instance by reviewing its storage configuration and considering if the current size is truly necessary for your workload. Reducing the storage allocated to the database could significantly lower your largest cost.  
  Rating: __/5
- *3 t3.large servers behind a load balancer, plus a 200GB gp3 volume* (llm:gemma3:4b, 1 call(s)): Reduce the number of t3.large instances to two, as three instances represent 85% of your bill. Evaluate if the current workload truly requires this level of compute capacity.  
  Rating: __/5
- *an EKS cluster with 3 m5.large nodes* (llm:gemma3:4b, 1 call(s)): Reduce the number of ec2 m5.large instances to two, as three are significantly contributing to your cloud spend. Evaluate if the current workload can be effectively handled with a smaller instance count.  
  Rating: __/5

### qwen2.5:3b
- *one m5.large web server, a multi-AZ mysql db.r5.large database, and 1 NAT gateway* (llm:qwen2.5:3b, 1 call(s)): Consider scaling down or terminating the RDS instance to a smaller size that better fits your current resource needs, as it's the largest cost item in your bill.  
  Rating: __/5
- *3 t3.large servers behind a load balancer, plus a 200GB gp3 volume* (rules, 2 call(s)): Your compute is the top cost. Try Graviton (t4g/m7g) instances, shut down non-prod instances outside working hours, and right-size using CloudWatch CPU data.  
  Rating: __/5
- *an EKS cluster with 3 m5.large nodes* (llm:qwen2.5:3b, 1 call(s)): Consider optimizing your EC2 m5.large instances by upgrading to a more powerful instance type or terminating idle instances to reduce costs.  
  Rating: __/5
