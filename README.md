# Cloud Guard AI

A 7-agent AWS cost & security console with a radar-style UI, powered by **AWS Bedrock (Claude)**.
Pick any month — historical months show **actual cost per service + total**; future months show
an **AI-reasoned forecast per service + projected total**, charted against trailing actuals.
A Resource Auditor inventories every resource the account is currently running across 17
categories — EC2 (running and stopped), EBS, Elastic IPs, NAT Gateways, Security Groups (every
ingress rule checked for internet exposure), DynamoDB, ElastiCache, CloudFront, SNS, SQS, RDS,
Lambda, ECS, EKS, ECR images + vulnerability scan findings, S3, every load balancer type — and
Claude reviews the whole inventory for concrete fixes. Every resource carries a real status
(EC2 running/stopped, EBS in-use/available, etc.) and a computed severity, so critical findings
(open security groups, public RDS, vulnerable ECR images) are visually highlighted rather than
buried in a flat list. Every block of Claude-generated text in the UI is labeled **"Claude
Bedrock"** so it's always clear which parts of the page are raw AWS data vs. AI output. Every
scan ends with a **Synthesizer** pass that reads every other agent's *structured* output (not
just their text) in one more Bedrock call and surfaces cross-cutting insights (e.g. a cost
spike that lines up with a security finding) as a ranked priority list. Runs on a single EC2
box via Docker Compose.

```
EC2 (t3.large, eu-central-1)
└── docker compose
    ├── frontend  (React + nginx)   :80   → proxies /api to backend
    └── backend   (Python, 7 agents) :3001 → Bedrock + Cost Explorer + Athena (CUR) + Security Hub
         ↑ EC2 instance role (no keys, auto-refreshed via IMDS)
```

---

## What's new (changelog)

Plain-language summary of everything added on top of the original MVP, newest first:

- **DocumentDB collection drill-down (new) — expand an RDS row to see a sample of its
  actual documents.** The `rds-rms-dev-db-cluster-instane-1` row in Resource Inventory is now
  clickable: expanding it shows the first 20 documents (plus a total count) from its
  `consentdb.consent` collection, fetched live via a direct DocumentDB connection
  (`pymongo`, TLS, AWS's public CA bundle — downloaded once and cached). Credentials come from
  `DOCDB_URI`, an environment variable only (never hardcoded, never committed — see
  `.env.example`), since DocumentDB has no IAM-token login like RDS/Aurora. **Note:** this app
  has no authentication of its own and is reachable on the open internet, so this shows real
  application data (not just infra metadata) to anyone who can reach the console — treat that
  as a deliberate, accepted risk for this dev environment, not an oversight.
- **Backend container now ships the AWS CLI, and the host's kubeconfig is mounted in (read-only).**
  `backend/Dockerfile` installs `awscli` v2, and `docker-compose.yml` mounts
  `/home/ec2-user/.kube` into the container at `/root/.kube:ro`. This is infra scaffolding for
  manual `kubectl` debugging on the box — the app's own Kubernetes calls (see the workloads
  feature below) build their connection entirely in Python from a fresh signed token, so they
  don't read or depend on this mounted kubeconfig at all.
- **EKS node/pod drill-down (new) — expand a cluster row to see what's actually running.**
  The Resource Inventory table's EKS rows are now clickable for the two clusters this console
  knows about (`doc-dev-eks-cluster` → `doc-dev` namespace, `rds-rms-dev-eks-cluster` →
  `rds-rms-dev` namespace, matched case-insensitively against whatever AWS returns).
  Expanding shows that cluster's nodes (status, instance type, AZ) and the pods running in its
  mapped namespace (status, ready count, node, restarts), fetched live from the Kubernetes API
  using a token signed from the backend's own AWS credentials — no kubeconfig or extra secrets
  needed. Requires the backend's AWS principal to have Kubernetes access on the cluster (an EKS
  access entry or `aws-auth` ConfigMap entry with at least view permissions) — until that's
  granted, expanding a row shows a clear inline error instead of silently failing. Kubernetes
  calls carry an explicit 5s connect / 10s read timeout, so an unreachable cluster fails fast
  instead of stalling the whole scan past nginx's proxy timeout.
- **Stop button.** A scan can now be cancelled mid-flight instead of having to wait it out.
- **Cost numbers no longer change when you switch region.** Billing is account-wide in AWS —
  there's no such thing as "the bill for us-east-1" — so the Cost/Anomaly/Forecast numbers now
  always show the full account total. The region picker still correctly filters things that
  *are* regional, like EC2 instances and security groups.
- **Severity on every resource and finding.** Every resource and every finding now carries a
  high/medium/low severity, and every high-severity one always comes with a Claude-written fix
  — nothing critical is ever left unexplained.
- **"Claude Bedrock" labels.** Every piece of text that came from the AI (not just raw AWS
  data) is now visibly tagged "Claude Bedrock" in the UI, so you always know what's a fact from
  AWS vs. an AI's interpretation of that fact.
- **Resource Auditor agent (new) — lists everything running in the account.** One new agent
  inventories 17 kinds of resources (servers, storage, databases, networking, containers,
  container images, messaging) and Claude reviews the whole list for problems and fixes. See
  the full list below.
- **Security Groups are now audited.** Every firewall rule is checked for whether it's open to
  the whole internet, and whether it exposes something sensitive (SSH, RDP, a database port).
  Risky rules are flagged high-severity with a specific suggested fix.
- **Stopped EC2 instances and all load balancer types are now included** — previously the
  inventory only showed *running* EC2 instances and modern load balancers, missing stopped
  servers (which still cost money for their attached storage) and older "Classic" load
  balancers.
- **Forecasting is now done by Claude, not a spreadsheet formula.** Previously, future-month
  cost predictions came from a simple compound-growth calculation, and Claude only wrote a
  summary of it. Now Claude looks at the actual trend data itself and produces its own
  prediction — the old calculation is kept only as a sanity-check reference. The forecast also
  now has a proper chart (trend line + projected point with a range band) instead of just a
  table of numbers.
- **Anomaly detection is now judged by Claude, not a fixed percentage rule.** Previously, any
  month with >25% cost increase was automatically flagged. Now that 25% rule is just a hint —
  Claude looks at the actual shape of the data and decides what's a real problem versus normal
  seasonal change, and always gives a concrete fix.
- **Synthesizer agent (new) — connects the dots across every other agent.** After all the
  agents you selected finish, one more Claude call reads everyone's results together and looks
  for things a single agent would miss — like a cost spike that lines up with a security
  problem. It produces a short "Executive analysis" with a ranked to-do list, shown at the top
  of every scan.
- **Removed the chat window.** The free-form "ask a question" chat box was removed in favor of
  the structured per-agent results above.

## The 7 agents
Every agent's "Suggestions" come from Claude reasoning over real tool data — none are
template/hardcoded text. Where an agent used to lean on a deterministic calculation (forecast
growth-rate, anomaly threshold), that calculation is now a cross-check reference only; Claude
makes the final call via a forced structured tool response (`submit_forecast`,
`submit_anomalies`, `submit_findings`, `submit_synthesis`) so the UI gets reliable JSON instead
of having to parse free text.

| Agent | Role | Model |
|-------|------|-------|
| Cost Analyst | Per-service spend + total — CUR (Athena) preferred, Cost Explorer fallback | Claude Sonnet |
| Anomaly Detector | Claude judges which months are genuine anomalies (a % MoM threshold is only a hint), investigates the driver service, gives a fix | Claude Sonnet |
| Rightsizing | Over-provisioned EC2/EKS/DocDB + savings | Claude Sonnet |
| Forecasting | Reasons over 24mo trend per service and submits its own structured projection (not just a copied growth-model number) — current/future months only | Claude Sonnet |
| Security | SecurityHub + GuardDuty findings | Claude Sonnet |
| Resource Auditor | Inventories EC2/EBS/EIP/NAT/Security Groups/DynamoDB/ElastiCache/CloudFront/SNS/SQS/RDS/Lambda/ECS/EKS/ECR (+ image vuln scans)/S3/ELB currently running, Claude reviews the lot for concrete fixes — current/historical months only | Claude Sonnet |
| Synthesizer | Reads every other agent's structured output from the same scan and surfaces cross-cutting insights + a ranked priority list | Claude Sonnet |

---

## Claude Bedrock AI features

Every piece of judgment in this app — every "why," every "is this risky," every suggested fix —
comes from a real call to Claude on AWS Bedrock, grounded in real AWS data pulled moments
before. Nothing is templated or hardcoded. Here's everywhere Claude is actually used:

1. **Cost Analyst** — explains the cost breakdown and gives 2-3 concrete cost-optimization
   suggestions tied to the actual top-spending services.
2. **Anomaly Detector** — judges which months are genuine spend anomalies (a fixed % threshold
   is only a hint, not the verdict), investigates the actual driver service behind each one, and
   gives a fix per anomaly.
3. **Rightsizing** — checks real EC2 CPU utilization before recommending a smaller instance
   size, with an estimated monthly saving — never guesses from spend alone.
4. **Forecasting** — reasons over 24 months of trend data per service and submits its own
   structured projection (not a copied growth-model number), with a confidence score and the
   top driver services.
5. **Security** — prioritizes SecurityHub/GuardDuty findings (public buckets, IAM wildcards,
   open security groups, GuardDuty alerts) and writes a remediation per finding.
6. **Resource Auditor** — reviews a full 17-category resource inventory (including every
   security group's firewall rules and every ECR image's vulnerability scan) and writes a
   concrete fix for every high-severity issue found — none get skipped.
7. **Synthesizer** — one more Claude call, at the end of every scan, reads every other agent's
   *structured* results together and finds cross-cutting insights a single agent would miss
   (e.g. a cost spike that lines up with a security finding), producing a ranked "Executive
   analysis."

**How the AI integration is engineered (not just "call the API"):**
- **Structured output, not free text.** Forecasting, Anomaly Detection, the Resource Auditor,
  and the Synthesizer all force Claude through a tool-call (`submit_forecast`,
  `submit_anomalies`, `submit_findings`, `submit_synthesis`) so it returns a structured JSON
  object instead of prose — the UI never has to parse free text to find a number.
- **Agentic tool-use loop.** Each agent hands Claude a set of real AWS-data tools (e.g.
  `get_cost_by_service`, `get_ec2_cpu_utilization`, `get_security_hub_findings`) and lets it
  call them across multiple turns to gather whatever it needs before answering — see
  `run_agent_loop` in `backend/src/bedrock/bedrock_client.py`.
- **Deterministic numbers are demoted to cross-checks.** Where a plain calculation used to be
  the source of truth (the old growth-rate forecast, the 25%-change anomaly threshold), Claude
  now makes the final call — the old calculation is kept only as a sanity-check reference and a
  fallback if Claude doesn't return a structured answer.
- **Grounded, not invented.** Every agent's system prompt explicitly instructs Claude never to
  invent a number, CVE, or finding that isn't in the data it was actually given.
- **Model routing.** Claude Sonnet handles all analysis; Claude Haiku is the cheap option for
  simple routing; Claude Opus is available for deeper reasoning if you need it.
- **Prompt caching.** System prompts can be cached for up to ~90% input-token savings on
  repeated scans.
- **Labeled in the UI.** Every block of Claude-generated text is visibly marked "Claude
  Bedrock" so it's always clear what's a fact straight from AWS vs. Claude's interpretation of
  that fact.

---

## How a scan flows through the system (plain-language version)

```
 YOU (browser)
   │  pick a month + region + scan target, click INITIATE
   ▼
 FRONTEND (React radar UI)
   │  sends one request: "scan this month/region for this target"
   ▼
 BACKEND (FastAPI)
   │  fans out to whichever agents you asked for, all at once
   ▼
 ┌────────────────────────────────────────────────────────────┐
 │  Cost Analyst · Anomaly Detector · Rightsizing             │
 │  Forecasting  · Security         · Resource Auditor        │
 └────────────────────────────────────────────────────────────┘
   │  EACH agent, independently:
   │    1. calls the real AWS APIs for its own data
   │       (Cost Explorer, EC2, Security Hub, ECR scans, etc.)
   │    2. hands that real data to Claude on Bedrock
   │    3. Claude reasons about it and returns a verdict +
   │       a concrete suggestion (never a canned/generic tip)
   ▼
 SYNTHESIZER (one more Claude Bedrock call)
   │  reads every agent's results together (not just their text,
   │  the actual structured numbers) and looks for connections a
   │  single agent can't see — e.g. "this cost spike lines up
   │  with that security finding"
   ▼
 BACKEND bundles everything into one response
   ▼
 FRONTEND renders the results:
   - tables of raw AWS facts → no "Claude Bedrock" tag
   - Claude's analysis & fix suggestions → tagged "Claude Bedrock"
   - every resource/finding is highlighted by severity (high/medium/low),
     and every high-severity one is always paired with a fix
   - if ANYTHING is high-severity anywhere → the whole UI switches into
     an "alert mode" theme so it's impossible to miss
```

In short: **AWS gives the facts, Claude gives the judgment**, and the UI always makes clear
which is which.

---

## Recommended infrastructure

### EC2 instance type
**Start with `t3.large` (2 vCPU, 8 GB RAM)** — ~$66/mo on-demand in eu-central-1.
The app is I/O-bound (waiting on AWS APIs and Bedrock), not compute-bound — the LLM
inference runs in Bedrock, not on your box. t3 burstable fits a scan tool perfectly:
idle most of the time, brief bursts during scans.

| Instance | vCPU | RAM | ~$/mo | When |
|----------|------|-----|-------|------|
| t3.medium | 2 | 4 GB | ~$33 | Testing only |
| **t3.large** | 2 | 8 GB | ~$66 | **Recommended start** |
| m5.large | 2 | 8 GB | ~$87 | If you exhaust CPU credits (non-burstable) |
| t3.xlarge | 4 | 16 GB | ~$133 | Heavy concurrency / added Redis |

Watch `CPUCreditBalance` in CloudWatch; switch to m5.large if it drains during scans.
Buy a 1-year Savings Plan once settled (~40% off).

### Bedrock model
**All 7 agents default to Claude Sonnet.** There's no routing/orchestrator layer — each scan
target calls its agent directly, so there's nothing cheap to route *to* a different model.
`BEDROCK_MODEL_HAIKU` is still defined in `bedrock_client.py` and available to pass as
`model=MODELS["HAIKU"]` if you want to move any individual agent to a cheaper model, but none
currently do.

| Use | Model | Rate (in/out per 1M tok) |
|-----|-------|--------------------------|
| All 7 agents (default) | Claude **Sonnet** | ~$3 / $15 |
| Available, currently unused | Claude **Haiku** | ~$1 / $5 |
| Optional deep reasoning | Claude **Opus** | ~$5 / $25 |

A full scan ≈ $0.12 on Sonnet; your EC2 will cost more than the LLM.
Enable **prompt caching** on the agents' system prompts for up to ~90% input savings.
Confirm the exact model IDs in your account/region (see step 1) — model strings change.

---

## Sequential deployment steps

### 1. Enable Claude on Bedrock (the "Model access" page is retired)
As of Sep 2025 AWS auto-enables serverless models — there is no Model access page to
click through anymore. Two things still apply for Claude specifically:

- **Submit the Anthropic one-time use-case form.** In the Bedrock console (eu-central-1),
  open any Claude model in the playground and submit the first-use form (or call
  `PutUseCaseForModelAccess`). Access is granted immediately; if you submit it at your
  AWS Organization's management account it is inherited by member accounts.
- **Marketplace auto-subscribe** happens on first invocation (can take up to ~15 min),
  which is why the instance role below includes `aws-marketplace:*` read/subscribe perms.

Confirm the model IDs available to you and put them in `.env`:
```bash
aws bedrock list-foundation-models --region eu-central-1 \
  --query "modelSummaries[?contains(modelId,'claude')].modelId" --output table
```
Governance note: model access is now controlled with IAM policies / SCPs, not a dashboard.

### 2. Create the IAM role for EC2
```bash
cat > trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
"Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON

aws iam create-role --role-name CloudGuardAI-EC2 \
  --assume-role-policy-document file://trust.json
aws iam put-role-policy --role-name CloudGuardAI-EC2 \
  --policy-name CloudGuardAI-Perms \
  --policy-document file://infra/iam-policy.json
aws iam create-instance-profile --instance-profile-name CloudGuardAI-EC2
aws iam add-role-to-instance-profile \
  --instance-profile-name CloudGuardAI-EC2 --role-name CloudGuardAI-EC2
```
**Already deployed?** `infra/iam-policy.json` gained a `ResourceInventoryRead` statement
(describe/list-only — EC2 volumes/addresses/NAT/**security groups**, DynamoDB, ElastiCache,
CloudFront, SNS, SQS, RDS, Lambda, ECS, ECR, S3, ELB) for the Resource Auditor agent. Re-run
just the `put-role-policy` command above against your existing role to pick it up — it's
idempotent.

### 3. Push this project to GitHub (from your laptop)
```bash
cd cloud-guard-ai
git init && git add . && git commit -m "Initial Cloud Guard AI"
git remote add origin https://github.com/<you>/cloud-guard-ai.git
git branch -M main && git push -u origin main
```
(`.gitignore` keeps `.env` and `node_modules` out of the repo.)

### 4. Launch the EC2 instance
- AMI: **Amazon Linux 2023**
- Type: **t3.large**
- IAM instance profile: **CloudGuardAI-EC2** (step 2)
- Security group inbound: **80** (HTTP) + **22** (SSH), both from your IP
- Storage: 20 GB gp3
- (optional) paste `infra/ec2-userdata.sh` into Advanced details → User data to auto-deploy on boot

### 5. Clone on the box
```bash
ssh ec2-user@<EC2_PUBLIC_IP>
sudo dnf install -y git docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# install the compose plugin (see infra/ec2-userdata.sh), then log out/in

git clone https://github.com/<you>/cloud-guard-ai.git   # use a PAT/deploy key if private
cd cloud-guard-ai
```

### 6. Configure and run
```bash
cp .env.example .env       # edit only if your Bedrock model IDs differ
docker compose up -d --build
docker compose ps          # both containers "running"
docker compose logs -f
```

### 7. Verify
```bash
curl localhost/api/health      # {"ok":true}
curl localhost/api/identity    # your account + assumed role
```
Open **http://<EC2_PUBLIC_IP>/** — radar loads, identity bar fills from STS,
**Initiate** runs a live scan.

### 8. Redeploy after changes
```bash
cd cloud-guard-ai && git pull && docker compose up -d --build
```

---

## CUR via Athena setup (exact bill match)

To get cost numbers that match the Bills page exactly, set up a Cost & Usage Report
with Athena integration in the Billing console:

1. **Billing console → Data Exports → Create export.** Choose Standard data export,
   pick Athena as the destination, and an S3 bucket (e.g. `s3://cloud-guard-ai/cloud-guard-cur/`).
   This auto-creates a Glue database/table and a crawler CloudFormation stack
   (`crawler-cfn.yml` lands in the bucket).
2. **Partition by billing period.** The export writes data to
   `.../data/billing_period=YYYY-MM/...` (Hive-style). The standard AWS Athena
   integration template uses **partition projection** — `billing_period` is computed
   on the fly from `TBLPROPERTIES`, not registered via `MSCK REPAIR`/`SHOW PARTITIONS`
   (those commands will error on a projected table — that's expected, not a bug).
3. **Same region for everything.** The S3 bucket, Glue database, and the Athena query
   results location must all be in the same AWS region — Athena rejects a
   query-results bucket in a different region from where the query runs
   ("S3 location... is invalid"). Set `ATHENA_REGION` to match.
4. **IAM permissions** — see `infra/iam-policy.json` (`AthenaQueryExecution`,
   `GlueCatalogReadOnly`, `CloudGuardAiBucketLevel`/`ObjectLevel`). The
   `s3:GetBucketLocation` permission on the bucket itself (not `/*`) is easy to miss
   and causes "Unable to verify/create output bucket".
5. Verify with a direct query:
   ```sql
   SELECT billing_period, SUM(line_item_unblended_cost)
   FROM cur_db.aws_cur WHERE billing_period = '2026-06' GROUP BY billing_period;
   ```
6. Test the app's endpoint directly: `curl localhost/api/cur-cost?month=2026-06`
   should return `{ billingPeriod, totalCost, services: [{ service, usage_cost,
   actual_cost, discount }, ...] }` matching the Bills page total.

Note: CUR only has data from whenever the export/partitioning was set up onward —
months before that fall back to Cost Explorer automatically (see `_get_cost_data` in
`cost_analyst.py`). `anomaly_detector.py`/`forecasting.py` still run on Cost Explorer
since they need 12-24 months of trend history CUR doesn't have yet.

---

## How it works

**Per-service cost (historical month):** Cost Analyst first tries the CUR (Cost &
Usage Report) via Athena — `SUM(line_item_unblended_cost)` and
`SUM(line_item_net_unblended_cost)` grouped by `line_item_product_code`, filtered on
the `billing_period` partition. This is the literal billed line-item data, so the
total reconciles exactly with the Bills page (Cost Explorer's `NetAmortizedCost` can
still drift for EDP/private-rate discounts on a linked account). If CUR has no data
for that month yet (e.g. months before the export was partitioned), or the query
fails, it falls back to `ce:GetCostAndUsage` (`NetAmortizedCost`) grouped by SERVICE.
The UI renders a table with bars and a TOTAL row — when CUR data is available, it
shows **Usage Cost / Actual Cost / Discount** per service; otherwise a single Cost
column.

**Cost figures ignore the region selector — billing is account-wide, not regional.**
`get_cost_by_service`/`get_monthly_trend`/`get_service_trend`/`get_cost_forecast` in
`cost_explorer_tools.py` never filter by AWS's `REGION` dimension, even though they accept a
`region` argument. A region filter there would silently undercount: many services bill as
global (S3, CloudFront, Route53, Support plans, tax) and don't tag to any region, so filtering
by `us-east-1` would exclude them from the total instead of giving you "the cost of
us-east-1" — there's no such concept as a per-region bill, only the account total. The region
picker still scopes everything that *is* genuinely regional: EC2/EBS/RDS/etc. inventory,
security groups, rightsizing, CloudWatch metrics.

**Month-over-month comparison:** when scanning the current (in-progress) month, the
cost block also fetches the previous month's total and attaches a `comparison` object
(`previousMonth`, `previousTotal`, `delta`, `deltaPct`) — shown as a badge above the
cost table.

**Forecasting is restricted to the current or future months** — a month that has
already ended can't be meaningfully forecast, so the UI disables the Forecast target
and the backend (`is_past_month`) returns a clear message instead of a number for
closed months. The month list itself is built dynamically from `new Date()` (24
months back, 6 forward), so the app rolls forward automatically — no hardcoded "current
month" to update by hand.

**Per-service forecast (future month) is AI-generated:** the backend still computes a
deterministic compound-growth projection (`forecastByService`) and AWS's native
`ce:GetCostForecast`, but only as cross-check reference points — the Forecasting agent
is handed the 24-month trend, each service's growth model, and the AWS ML number, then
reasons about which services will accelerate/taper and calls a `submit_forecast` tool
with its own per-service projection, range and confidence. The UI charts the trailing
actuals against the projected month (with a low–high band) and renders a PROJECTED TOTAL row,
visually distinct from the historical cost table. If the agent doesn't return a structured
forecast (e.g. it hits the turn limit), the deterministic growth-model number is used as a
fallback so the UI never shows nothing.

**Resource inventory (current/historical months) is also AI-reviewed:** `get_full_inventory`
gathers every resource type the IAM role can see, in parallel, each category isolated so one
disabled service doesn't blank the rest:
- **Compute/network:** EC2 instances — **running and stopped, not just running** (flags
  stopped instances, since they're still billed for attached EBS storage), EBS volumes (flags
  unattached — still billed), Elastic IPs (flags unassociated — still billed), NAT Gateways,
  every load balancer — **ALB/NLB/GWLB (elbv2) and legacy Classic LBs (elb v1), internet-facing
  and internal alike**, not just internet-facing ALBs
- **Network security:** Security Groups — every ingress rule is checked for exposure to
  `0.0.0.0/0`/`::/0`; a rule opening a sensitive port (SSH, RDP, MySQL, Postgres, MSSQL,
  MongoDB, Redis, Elasticsearch, VNC, Telnet, FTP) or all ports/protocols is `severity: high`,
  any other internet-open rule is `medium`
- **Data:** RDS (flags publicly-accessible → `high`), DynamoDB, ElastiCache
- **Containers:** ECS (flags desired≠running task count → `medium`), EKS (flags public API
  endpoint → `high`; for `doc-dev-eks-cluster` and `rds-rms-dev-eks-cluster`, the row expands
  in the UI to show live nodes + application-namespace pods — see the changelog above)
- **Images:** ECR repos, with the latest pushed image's vulnerability scan severity counts
  (`high` if any CRITICAL/HIGH CVE, `low` if only lesser ones)
- **Storage/edge/messaging:** S3 (flags public-access-block → `high` / missing default
  encryption → `medium`), CloudFront, SNS, SQS

Every resource carries a real status string from its AWS API (EC2 `running`/`stopped`, EBS
`in-use`/`available`, security groups `restricted`/`open-to-internet`, etc.) plus a computed
`severity` (`high`/`medium`/`low`/none) that the UI uses to highlight rows by how urgent they
are. Those flags/severities are only a starting point handed
to Claude alongside the *entire* inventory (every category, not just the flagged resources);
the Resource Auditor agent calls `submit_findings` with its own judged list of concrete fixes
(e.g. "remove the 0.0.0.0/0:22 ingress rule, use SSM Session Manager instead" for an open
security group, "rebuild from a patched base image" for a vulnerable ECR image, or "delete this
unattached volume" for an idle EBS disk), citing real figures from the data — it's told
explicitly not to invent a CVE or issue the data doesn't support. Not available for future
months (there's nothing "currently running" to inventory yet). The findings render in their own
"Claude Bedrock — Resource fixes" block, separate from the raw inventory table, so it's clear
which part is AWS fact and which is Claude's judgment.

**Anomaly detection is judged by Claude, not a fixed threshold:** a >25% month-over-month
change is computed in Python as a hint, but the Anomaly Detector agent decides which flagged
months are genuine anomalies (vs. normal seasonal variation), can flag a month the heuristic
missed, calls `get_cost_by_service` to find the actual driver, and submits its verdict via
`submit_anomalies` — each entry carries a concrete fix suggestion, not just an explanation.

**Synthesizer (every scan):** once the requested agents finish, their structured outputs
(not just their text summaries) are handed to one more Bedrock call whose only job is to
find connections a single-domain agent can't see — e.g. a cost spike that lines up with a
security finding, or a rightsizing candidate that explains part of a forecasted increase.
It returns a headline, a short cross-agent narrative, and a ranked priority list shown at
the top of the results as "Executive analysis."

**Every Claude-generated block is labeled "Claude Bedrock" in the UI** (`bedrockTitle()` in
`RadarConsole.jsx`) — the executive analysis, every per-agent analysis/findings block, the
forecast (when AI-generated), and the resource fixes block all carry the tag. Tables of raw AWS
data (the historical cost table, the resource inventory table) deliberately don't, so it's
always visually obvious which numbers are AWS fact and which are Claude's interpretation of
that fact.

**Auth (no key management):** the backend holds no AWS keys. The SDK reads temporary
credentials from the EC2 instance role via IMDS, and AWS rotates them automatically.
Your hourly SSO refresh is only for your laptop's CLI — unrelated to the running app.

---

## Project layout
```
cloud-guard-ai/
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/
│       ├── main.py                       # /api/identity /api/scan (FastAPI)
│       ├── bedrock/bedrock_client.py     # invoke_claude + tool-use loop
│       ├── agents/                       # 6 specialists + synthesizer
│       └── tools/                        # cost_explorer (per-service + forecast), athena_cur (exact bill match), security, sts, resource_inventory (17 categories + EKS node/pod workloads via the `kubernetes` client — see the agents table above), ec2, cloudwatch
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/RadarConsole.jsx          # radar UI + per-service cost/forecast tables
└── infra/
    ├── iam-policy.json               # instance-role permissions
    └── ec2-userdata.sh               # boot bootstrap (installs docker, clones, up)
```

## Local development (no EC2)
```bash
# backend (needs AWS creds — your SSO profile works)
cd backend && pip install -r requirements.txt && export AWS_PROFILE=DT_DTRD_DEV
uvicorn src.main:app --reload --port 3001
# frontend (proxies /api to :3001)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## Next steps (not in this MVP)
- Redis (ElastiCache) for the Cost Explorer cache — uncomment in docker-compose.
- DynamoDB for scan history, findings lifecycle, forecast-vs-actual accuracy.
- ALB + ACM certificate for HTTPS; restrict the SG to the ALB.
