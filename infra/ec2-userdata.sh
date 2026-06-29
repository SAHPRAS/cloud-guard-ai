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
