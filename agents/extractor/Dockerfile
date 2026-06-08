# ExtractorAgent container — ARM64, kernel installed editable (Spec/09 §5 + BUILD §2.3).
# Build context is the REPO ROOT so the kernel can be copied in:
#   docker build -f agents/extractor/Dockerfile -t laboraid-extractor .
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/kernel:/opt/agent \
    PROFILES_DIR=/opt/profiles \
    AGENT_SCRATCH=/tmp/agent-runs

# System deps for pdfplumber / rapidocr (kernel document processing).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Kernel (Ashwani's deterministic pipeline) — loaded via PYTHONPATH only,
# never installed as a Python package. The kernel sets `[tool.uv] package = false`
# in its pyproject.toml to declare this; `uv pip install -e` ignores that flag and
# falls through to setuptools, which fails on the kernel's flat layout (5 top-level
# dirs: data, extract, profiles, pipeline, canonical). So we install the kernel's
# *runtime dependencies* explicitly and let PYTHONPATH=/opt/kernel make `canonical`
# and `pipeline` importable.
COPY kernel/ /opt/kernel/
RUN uv pip install --system \
    "pdfplumber" "openpyxl" "pandas" "pyyaml" \
    "pypdfium2" "rapidocr-onnxruntime" "Pillow"

# Agent code + its dependencies.
COPY agents/extractor/ /opt/agent/
RUN uv pip install --system \
    "strands-agents>=0.1.0" "bedrock-agentcore>=0.1.0" "boto3>=1.34.0" "pyyaml>=6.0"

# Per-union Profile YAMLs available at /opt/profiles/<union>.yaml.
COPY kernel/profiles/ /opt/profiles/

WORKDIR /opt/agent

# AgentCore Runtime entrypoint — BedrockAgentCoreApp serves on :8080.
EXPOSE 8080
CMD ["python", "agent.py"]
