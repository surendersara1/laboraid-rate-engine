# ImproverAgent container — ARM64, Strands on Bedrock AgentCore Runtime.
# Build context is the REPO ROOT (so shared rate_math is copied in):
#   docker build -f agents/improver/Dockerfile -t laboraid-improver .
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/agent

# Shared deterministic core — the SAME rate_math the synthesizer uses, so an
# improved sheet is byte-identical to how the original was produced.
COPY lambdas/shared/rate_math.py /opt/agent/rate_math.py

# Agent code + deps. pypdf for reading source PDFs during comment re-synthesis.
COPY agents/improver/ /opt/agent/
RUN uv pip install --system \
    "strands-agents>=0.1.0" "bedrock-agentcore>=0.1.0" "boto3>=1.34.0" "pypdf>=4.0"

WORKDIR /opt/agent
EXPOSE 8080
CMD ["python", "agent.py"]
