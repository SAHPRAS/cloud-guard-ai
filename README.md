# Cloud Guard AI

A 6-agent AWS cost & security console with a radar-style UI, powered by **AWS Bedrock (Claude)**.
Pick any month — historical months show **actual cost per service + total**; future months show
**forecasted cost per service + projected total**. Runs on a single EC2 box via Docker Compose.

```
EC2 (t3.large, eu-central-1)
└── docker compose
    ├── frontend  (React + nginx)   :80   → proxies /api to backend
    └── backend   (Python, 6 agents) :3001 → Bedrock + Cost Explorer + Athena (CUR) + Security Hub
         ↑ EC2 instance role (no keys, auto-refreshed via IMDS)
```

## The 6 agents
| Agent | Role | Model |
|-------|------|-------|
| Orchestrator | Routes chat queries to the right agent | Claude Haiku (cheap) |
| Cost Analyst | Per-service spend + total — CUR (Athena) preferred, Cost Explorer fallback | Claude Sonnet |
| Anomaly Detector | Flags month-over-month spikes | Claude Sonnet |
| Rightsizing | Over-provisioned EC2/EKS/DocDB + savings | Claude Sonnet |
| Forecasting | Per-service forecast + projected total (current/future months only) | Claude Sonnet |
| Security | SecurityHub + GuardDuty findings | Claude Sonnet |

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
**Default to Claude Sonnet, route the orchestrator to Claude Haiku.**

| Use | Model | Rate (in/out per 1M tok) |
|-----|-------|--------------------------|
| Orchestrator (routing) | Claude **Haiku** | ~$1 / $5 |
| All analysis agents | Claude **Sonnet** | ~$3 / $15 |
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

**Per-service forecast (future month):** `forecastByService` pulls 6 months of
per-service history, derives EACH service's own growth rate, projects each forward to
the target month, and sums them → UI shows projected cost per service, a low–high range,
confidence %, and a PROJECTED TOTAL row (blue = forecast). Also cross-checked against
the AWS-native `ce:GetCostForecast` ML model.

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
│       ├── main.py                       # /api/identity /api/scan /api/query (FastAPI)
│       ├── bedrock/bedrock_client.py     # invoke_claude + tool-use loop
│       ├── agents/                       # orchestrator + 5 specialists
│       └── tools/                        # cost_explorer (per-service + forecast), athena_cur (exact bill match), security, sts
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
