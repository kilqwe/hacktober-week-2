# Model bake-off

8 plain-English descriptions with known answers, and 3 advisor tips per model. Seed and temperature are fixed, so runs are repeatable.

| Model | Inventories exactly right | Item F1 | Extraction errors | Avg extract time | Tip passed 1st try | Tip needed retry | Tip fell back to rules | Avg tip time |
|---|---|---|---|---|---|---|---|---|
| gemma3:4b | 5/8 | 91% | 0 failed, 3 dropped | 3.4s | 3/3 | 0/3 | 0/3 | 3.0s |
| qwen2.5:3b | 8/8 | 100% | 0 failed, 0 dropped | 2.9s | 2/3 | 0/3 | 1/3 | 3.6s |
| llama3.2:3b | 4/8 | 86% | 0 failed, 2 dropped | 3.0s | 0/3 | 3/3 | 0/3 | 6.0s |

## Tips written (rate each 1-5 for how useful and correct it is)

### gemma3:4b
- *one m5.large web server, a multi-AZ mysql db.r5.large database, and 1 NAT gateway* (llm:gemma3:4b, 1 call(s)): Optimize your RDS db.r5.large instance by reviewing its storage configuration and considering if the current size is truly necessary for your workload. Reducing the storage allocated to the database could significantly lower your largest cost.  

- *3 t3.large servers behind a load balancer, plus a 200GB gp3 volume* (llm:gemma3:4b, 1 call(s)): Reduce the number of t3.large instances to two, as three instances represent 85% of your bill. Evaluate if the current workload truly requires this level of compute capacity.  

- *an EKS cluster with 3 m5.large nodes* (llm:gemma3:4b, 1 call(s)): Reduce the number of ec2 m5.large instances to two, as three are significantly contributing to your cloud spend. Evaluate if the current workload can be effectively handled with a smaller instance count.  


### qwen2.5:3b
- *one m5.large web server, a multi-AZ mysql db.r5.large database, and 1 NAT gateway* (llm:qwen2.5:3b, 1 call(s)): Consider scaling down or terminating the RDS instance to a smaller size that better fits your current resource needs, as it's the largest cost item in your bill.  

- *3 t3.large servers behind a load balancer, plus a 200GB gp3 volume* (rules, 2 call(s)): Your compute is the top cost. Try Graviton (t4g/m7g) instances, shut down non-prod instances outside working hours, and right-size using CloudWatch CPU data.  

- *an EKS cluster with 3 m5.large nodes* (llm:qwen2.5:3b, 1 call(s)): Consider optimizing your EC2 m5.large instances by upgrading to a more powerful instance type or terminating idle instances to reduce costs.  


### llama3.2:3b
- *one m5.large web server, a multi-AZ mysql db.r5.large database, and 1 NAT gateway* (llm:llama3.2:3b, 2 call(s)): To optimize the RDS DB instance, consider upgrading to the RDS DB instance type that matches the current usage pattern, ensuring that the instance is not underutilized and taking advantage of the latest performance enhancements.  

- *3 t3.large servers behind a load balancer, plus a 200GB gp3 volume* (llm:llama3.2:3b, 2 call(s)): To optimize the usage of the EC2 instances, consider using Auto Scaling to dynamically adjust the number of instances based on the workload, ensuring that the instances are utilized to their full capacity and reducing the overall cost. This can be achieved by setting up a scaling policy that monitors CPU utilization and adjusts the instance count accordingly.  

- *an EKS cluster with 3 m5.large nodes* (llm:llama3.2:3b, 2 call(s)): To optimize the EC2 M5 Large instances, consider using the Spot Instances feature to take advantage of discounted pricing, which can help reduce costs. This can be done by setting up a Spot Fleet and allocating the instances to it, allowing the startup to secure discounted pricing while still having control over instance utilization.  
