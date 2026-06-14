#!/usr/bin/env python3
"""LaborAid Rate Engine — architecture diagram (diagram-as-code, AWS icons).

Renders the deployed system as a single cohesive diagram using the official AWS
icon set via the `diagrams` library (mingrammer). On-brand (LaborAid navy/gold).

Output: diagram/laboraid_architecture.png  and  .svg

Prereqs (installed once):
    - Graphviz  (winget install Graphviz.Graphviz)   — provides the `dot` engine
    - pip:      py -3 -m pip install diagrams

Run:
    py -3 scripts/arch_diagram.py

This is hand-authored to match the real system (it does not scan AWS); keep it in
sync with docs/LAMBDA_AND_AGENT_INVENTORY.md. For an auto-generated view from the
CDK templates, see diagram/cfn/ (produced by cfn-diagram).
"""
from __future__ import annotations

import os

# Make sure the Graphviz `dot` engine is found even if it isn't on PATH.
_GV = r"C:\Program Files\Graphviz\bin"
if os.path.isdir(_GV) and _GV not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _GV + os.pathsep + os.environ.get("PATH", "")

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda, EC2ContainerRegistry
from diagrams.aws.database import Aurora, Dynamodb
from diagrams.aws.integration import Eventbridge, StepFunctions
from diagrams.aws.management import Cloudwatch
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users

# --- LaborAid brand styling --------------------------------------------------
NAVY, GOLD = "#16295D", "#F8C431"
graph_attr = {
    "fontsize": "22",
    "fontname": "Helvetica",
    "fontcolor": NAVY,
    "bgcolor": "white",
    "pad": "0.6",
    "splines": "spline",
    "nodesep": "0.6",
    "ranksep": "0.9",
    "label": "LaborAid Rate Engine — AWS Architecture (us-east-2)\n",
    "labelloc": "t",
}
node_attr = {"fontname": "Helvetica", "fontsize": "11", "fontcolor": "#1b2548"}
edge_navy = Edge(color=NAVY)
edge_gold = Edge(color="#C9A21E", style="bold")
edge_tele = Edge(color="#94a3b8", style="dashed", label="telemetry")

OUT = os.path.join("diagram", "laboraid_architecture")

with Diagram(
    "LaborAid Rate Engine",
    filename=OUT,
    outformat=["png", "svg"],
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    users = Users("Admin & Business\nusers")

    with Cluster("Edge & Access"):
        cf = CloudFront("CloudFront")
        spa = S3("React SPA")
        cognito = Cognito("Cognito\n(role groups)")
        cf >> Edge(color=NAVY) >> spa

    with Cluster("API (HTTP)"):
        api = APIGateway("API Gateway")
        api_fns = Lambda("API Lambdas\n(22 routes)")
        api >> Edge(color=NAVY) >> api_fns

    with Cluster("Extraction pipeline · Step Functions"):
        sfn = StepFunctions("Plan → Synthesize\n→ Publish")
        planner = Lambda("batch-planner\n(Plan)")
        synth = Lambda("synthesizer\n(Synthesize)")
        publish = Lambda("synth-publish\n(Publish)")
        sfn >> edge_navy >> planner >> edge_navy >> synth >> edge_navy >> publish

    with Cluster("AI · Bedrock + AgentCore (Strands)"):
        bedrock = Bedrock("Bedrock\nOpus 4.5 / Sonnet 4.6\n+ PII guardrail")
        ecr = EC2ContainerRegistry("ECR\nagent images")
        extractor = Lambda("ExtractorAgent\n(AgentCore)")
        improver = Lambda("ImproverAgent\n(AgentCore)")
        ecr >> Edge(color=NAVY, style="dotted") >> extractor
        ecr >> Edge(color=NAVY, style="dotted") >> improver
        extractor >> edge_navy >> bedrock
        improver >> edge_navy >> bedrock
        synth >> edge_navy >> bedrock

    with Cluster("Data"):
        with Cluster("System of record"):
            aurora = Aurora("Aurora PostgreSQL\nrate sheets · corrections\naudit")
        with Cluster("Operational telemetry"):
            jobs = Dynamodb("DynamoDB\njobs · agent-config")
        s3in = S3("S3 inputs\n(PDFs)")
        s3out = S3("S3 outputs\n(CSV · Excel)")

    with Cluster("Read-model (CQRS)"):
        evb = Eventbridge("EventBridge")
        jobwriter = Lambda("job-writer")
        evb >> edge_navy >> jobwriter >> edge_navy >> jobs

    cw = Cloudwatch("CloudWatch\nlogs · traces")

    # --- flows ---------------------------------------------------------------
    users >> edge_navy >> cf
    users >> Edge(color=NAVY, label="HTTPS") >> api
    api >> Edge(color=NAVY, style="dotted", label="authZ") >> cognito

    # API → core
    api_fns >> Edge(color=NAVY, label="rate sheets") >> aurora
    api_fns >> Edge(color=NAVY, label="read jobs") >> jobs
    api_fns >> edge_navy >> sfn
    api_fns >> Edge(color="#C9A21E", style="bold", label="Improve") >> improver
    api_fns >> Edge(color=NAVY, label="presign") >> s3in

    # pipeline I/O
    s3in >> edge_navy >> planner
    publish >> Edge(color=NAVY, label="write rows") >> aurora
    publish >> Edge(color=NAVY, label="artifacts") >> s3out

    # agents I/O
    extractor >> Edge(color=NAVY) >> aurora
    extractor >> Edge(color=NAVY, style="dotted") >> s3in
    improver >> Edge(color="#C9A21E", style="bold", label="v+1 + change log") >> aurora

    # read-model + observability
    sfn >> Edge(color="#94a3b8", style="dashed", label="status") >> evb
    sfn >> Edge(color="#94a3b8", style="dashed") >> cw

print(f"wrote {OUT}.png and {OUT}.svg")
