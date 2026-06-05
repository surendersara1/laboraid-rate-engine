# ProfileDrafterAgent container — ARM64, kernel installed editable (Spec/09 §15.1).
# Build context is the REPO ROOT so the kernel can be copied in:
#   docker build -f agents/profile_drafter/Dockerfile -t laboraid-profile-drafter .
#
# Mirrors agents/extractor/Dockerfile structure. The drafter is the build-time
# agent that auto-generates per-union profile YAMLs and Python extractors so
# subsequent runs can use Path A (deterministic) instead of Path C (LLM).
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/kernel:/opt/agent \
    PROFILES_DIR=/opt/profiles \
    AGENT_SCRATCH=/tmp/agent-runs

# System deps for pdfplumber / openpyxl (groundtruth + Rate Notice parsing).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 git \
    && rm -rf /var/lib/apt/lists/*

# Kernel (Ashwani's deterministic pipeline) — installed editable, never modified
# by the drafter container itself; the drafter writes to /opt/kernel only when a
# generated profile/extractor passes validation and is being staged for PR.
COPY kernel/ /opt/kernel/
RUN uv pip install --system -e /opt/kernel

# Agent code + its dependencies.
COPY agents/profile_drafter/ /opt/agent/
RUN uv pip install --system \
    "strands-agents>=0.1.0" "bedrock-agentcore>=0.1.0" "boto3>=1.34.0" \
    "anthropic>=0.40.0" "openpyxl>=3.1.0" "pyyaml>=6.0" "jsii>=1.100.0"

# Per-union Profile YAMLs available at /opt/profiles/<union>.yaml (the drafter
# reads these as schema templates for the YAMLs it generates).
COPY kernel/profiles/ /opt/profiles/

WORKDIR /opt/agent

# AgentCore Runtime entrypoint — BedrockAgentCoreApp serves on :8080.
EXPOSE 8080
CMD ["python", "agent.py"]
