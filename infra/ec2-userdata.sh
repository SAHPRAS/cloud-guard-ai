#!/bin/bash
# EC2 user-data — paste into the "Advanced details > User data" box when launching,
# or run manually after SSH. Installs Docker, clones the app, brings it up.
set -euxo pipefail

# --- install docker + compose plugin (Amazon Linux 2023) ---
dnf update -y
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

DOCKER_CONFIG=/usr/local/lib/docker
mkdir -p $DOCKER_CONFIG/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o $DOCKER_CONFIG/cli-plugins/docker-compose
chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose

# --- get the app ---
cd /home/ec2-user
# Public repo:
git clone https://github.com/SAHPRAS/cloud-guard-ai.git
# Private repo (inject a token, ideally from SSM Parameter Store, not hardcoded):
#   GH_PAT=$(aws ssm get-parameter --name /cloudguard/gh_pat --with-decryption --query Parameter.Value --output text)
#   git clone https://<user>:${GH_PAT}@github.com/<you>/cloud-guard-ai.git
chown -R ec2-user:ec2-user /home/ec2-user/cloud-guard-ai
cd /home/ec2-user/cloud-guard-ai

# --- config ---
cp -n .env.example .env || true

# --- build + run ---
docker compose up -d --build

echo "Cloud Guard AI is up on port 80"





=====================================================================

Editor
Recent queries
Saved queries
Query settings
Data


Data source

AwsDataCatalog
Catalog

None
Database

cur_db
Tables and views
Create


Tables (1)

1


aws_cur

line_item_product_code
string

line_item_unblended_cost
double

line_item_net_unblended_cost
double


Views (0)

1

========================================================
cloud-guard-ai
cloud-guard-cur/
cloud-guard-cur/
Amazon S3
Buckets
cloud-guard-ai
cloud-guard-cur/
cloud-guard-cur/

Amazon S3
Buckets
General purpose buckets
Directory buckets
Table buckets
Vector buckets
Files
File systems
Access management and security
Access Points
Access Points for FSx
Access Grants
IAM Access Analyzer
Storage management and insights
Storage Lens
Batch Operations
Account and organization settings

AWS Marketplace for S3
cloud-guard-cur/
Copy S3 URI

Objects

Properties
Objects (4)

Copy S3 URI
Copy URL
Download
Open 
Delete
Actions
Create folder
Upload
Objects are the fundamental entities stored in Amazon S3. You can use Amazon S3 inventory  to get a list of all objects in your bucket. For others to access your objects, you'll need to explicitly grant them permissions. Learn more 


1


Name
	
Type
	
Last modified
	
Size
	
Storage class

Name
	
Type
	
Last modified
	
Size
	
Storage class

crawler-cfn.yml
yml
June 28, 2026, 21:34:34 (UTC+05:30)
9.8 KB
Standard
data/
Folder
-
-
-
execution_status/
Folder
-
-
-
metadata/
Folder

=====================================================================

service
usage_cost
actual_cost
savings
	
AmazonEKS
136.97
136.97
0.0
2
AWSELB
125.41
125.41
0.0
3
AmazonVPC
73.67
73.67
-0.0
4
AmazonInspectorV2
60.48
60.48
0.0
5
AmazonDocDB
52.03
52.03
-0.0
6
AmazonCloudWatch
39.12
39.12
-0.0
7
AWSSecurityHub
20.58
20.58
-0.0
8
AmazonEC2
19.87
19.87
-0.0
9
AmazonGuardDuty
19.44
19.44
0.0
10
AmazonECR
4.71
4.71
0.0
11
AWSConfig
2.92
2.92
-0.0
12
awskms
2.29
2.29
-0.0
13
AmazonBedrockService
2.26
2.26
0.0
14
AWSCostExplorer
2.22
2.22
0.0
15
AmazonDynamoDB
0.82
0.82
-0.0
16
AmazonRDS
0.21
0.21
-0.0
17
AWSXRay
0.13
0.13
0.0
18
AmazonRoute53
0.09
0.09
-0.0
19
AmazonS3
0.02
0.02
-0.0
20
AmazonApiGateway
0.01
0.01
0.0
21
AWSQueueService
0.0
0.0
-0.0
22
AmazonSNS
0.0
0.0
-0.0
23
AWSCloudTrail
0.0
0.0
0.0
24
awswaf
0.0
0.0
0.0
25
AWSDataTransfer
0.0
0.0
0.0
26
AWSCloudFormation
0.0
0.0
0.0
27
AWSEvents
0.0
0.0
-0.0
28
AWSSecretsManager
0.0
0.0
-0.0
29
AWSGlue
0.0
0.0
0.0
30
AWSLambda
0.0
0.0
-0.0
31
AmazonStates
0.0
0.0
0.0
32
TOTAL
563.25
563.25
-0.0

