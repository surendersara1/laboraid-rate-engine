# laboraid-cdk — AWS CDK (Python) infrastructure

Python CDK app for the LaborAid Rate Engine POC. Eight stacks wrap Ashwani's
deterministic extraction kernel plus one Strands `ExtractorAgent`.

**This is Python CDK** (`aws-cdk-lib`), not TypeScript. The only TypeScript in
the repo lives under `ui/`.

## Commands

```bash
cd cdk
uv sync                 # install deps + create .venv
uv run cdk synth        # synthesize all stacks (acceptance gate)
uv run cdk deploy --all # deploy (human's call; needs AWS creds + bootstrap)
```

## Layout

```
cdk/
├── app.py                       # entry: instantiates stacks, applies tag Aspect
├── cdk.json                     # { "app": "uv run python app.py" }
├── pyproject.toml               # deps (uv-managed)
└── laboraid_cdk/
    ├── aspects/mandatory_tags.py   # IAspect — 13 mandatory tags on every resource
    ├── config/{dev,prod}.py        # env-specific Config dataclass
    ├── util/naming.py              # name(env, layer, type_, purpose) -> str
    ├── constructs/                 # tagged L2/L3 wrappers + Strands agent runtime
    └── stacks/                     # 8 stacks (filled by build groups B–F)
```

Select the environment with CDK context: `uv run cdk synth -c env=prod`
(defaults to `dev`).
