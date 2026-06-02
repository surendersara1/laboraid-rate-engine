# Final push step — Bitbucket repo creation + push

The local monorepo is built, history is clean, both branches exist. The only thing left is creating an empty repo on Bitbucket and pushing.

---

## Step 1 — Create the empty repo on Bitbucket (~30 sec, manual)

1. Go to **https://bitbucket.org/northbay/workspace/repositories** (or wherever your `northbay` workspace lives)
2. Click **Create repository**
3. Settings:
   - **Repository name:** `laboraid-rate-engine`
   - **Access level:** Private
   - **Include a README?** **No** (we already have one locally)
   - **Default branch name:** `main`
   - **Include .gitignore?** **No** (we already have one locally)
   - **Include a license?** Skip
4. Click **Create repository**
5. Bitbucket will show you the URL — should be: `git@bitbucket.org:northbay/laboraid-rate-engine.git`

> If your workspace slug is **not** `northbay` (e.g., `nbsolutions` or `northbay-solutions`), let me know and I'll update the remote.

---

## Step 2 — Push from local (~5 sec, one command block)

Open a shell at `E:\NBS_LaborAid\laboraid-rate-engine` and run:

```bash
cd /e/NBS_LaborAid/laboraid-rate-engine

# Point at the new Bitbucket repo
git remote add origin git@bitbucket.org:northbay/laboraid-rate-engine.git

# Push both branches
git push -u origin main
git push -u origin feat/aws-strands-integration

# Confirm
git ls-remote origin
```

You should see both branches listed on the remote.

---

## What you'll have after the push

On Bitbucket, in the new `laboraid-rate-engine` repo:

**Branch: `main`**
- `b8abea9` chore: scaffold laboraid-rate-engine monorepo
- `7f3f1b4` Squashed 'kernel/' content from commit 0255055 (Ashwani's HEAD on `feat/cba-ratesheet-pipeline`)
- `fa452ee` feat: import labor_aid_poc kernel via git subtree

**Branch: `feat/aws-strands-integration`** (currently identical to `main`; this is where the AWS+Strands work will land)

**Repo layout:**
```
laboraid-rate-engine/
├── README.md
├── .gitignore
├── PUSH_INSTRUCTIONS.md        ← this file (delete after pushing if you want)
├── kernel/                      ← Ashwani's labor_aid_poc imported here
│   ├── canonical/
│   ├── data/                    ← per-union cba + ratesheet + ai_output
│   ├── extract/                 ← build_483.py, compare_483.py reference
│   ├── pipeline/                ← ingest, ocr, extract, compute, pivot, evaluate, run
│   ├── profiles/                ← 537, 483, 704 YAMLs
│   ├── .claude/                 ← planner/builder/evaluator harness
│   ├── DESIGN.md, README.md, SETUP.md
│   ├── pyproject.toml, uv.lock
│   └── .gitignore
├── cdk/                         ← (placeholder) AWS CDK stacks
├── agents/                      ← (placeholder) Strands ExtractorAgent on AgentCore
├── lambdas/                     ← (placeholder) API + validation + rendering Lambdas
├── ui/                          ← (placeholder) React admin SPA
├── containers/                  ← (placeholder) Docling + OCR containers
├── profiles/                    ← (placeholder) symlinks to kernel/profiles/*
├── docs/                        ← (placeholder) Architecture + runbook + onboarding
└── scripts/                     ← (placeholder) Deploy + bootstrap helpers
```

---

## Optional follow-ups

### Protect `main` branch on Bitbucket
- Bitbucket → Repository settings → Branch permissions
- Add restriction: `main` requires pull request, ≥1 approver, no force push
- Force push enabled on `feat/*` branches (for the team's working branches)

### Set up a project / pipelines later
- Bitbucket Pipelines for CI (lint, test, kernel regression) can be added in a later commit (`bitbucket-pipelines.yml`)
- AWS deployment via Bitbucket OIDC → AssumeRole into the LaborAid AWS account (no static keys)

### Pulling future kernel updates
If Ashwani pushes new commits to `labor_aid_poc`'s `feat/cba-ratesheet-pipeline` branch (e.g., he fixes the 537 reallocation or adds 281/821):

```bash
cd /e/NBS_LaborAid/laboraid-rate-engine
git checkout main
git remote add kernel-source git@bitbucket.org:northbay/labor_aid_poc.git  # if not already added
git fetch kernel-source feat/cba-ratesheet-pipeline
git subtree pull --prefix=kernel kernel-source feat/cba-ratesheet-pipeline --squash -m "chore: pull kernel updates from upstream"
git push origin main
```

### Push the kernel-source remote definition into the repo (optional)
The `kernel-source` remote is currently configured locally only on your machine. If teammates clone the new repo and want to pull kernel updates, they'll need to add the remote themselves. Document this in `docs/CONTRIBUTING.md` (to be created).

---

## Sanity checks before pushing

```bash
cd /e/NBS_LaborAid/laboraid-rate-engine

# Confirm clean working tree
git status

# Confirm both branches exist locally
git branch
# Expected:
# * feat/aws-strands-integration
#   main

# Confirm kernel imported correctly (should show ~55 files including pipeline/, profiles/, .claude/)
find kernel -maxdepth 2 -not -path "*/.git/*" | head -30

# Confirm history clean (3 commits on main, identical so far on feat branch)
git log --oneline --all --decorate
# Expected:
# fa452ee (HEAD -> feat/aws-strands-integration, main) feat: import labor_aid_poc kernel via git subtree
# 7f3f1b4 Squashed 'kernel/' content from commit 0255055
# b8abea9 chore: scaffold laboraid-rate-engine monorepo
```

If those all check out, you're ready to push.

---

## After push — first issue to file

Suggest creating Bitbucket issue **#1** with the following content (this is the build kickoff item):

> **Title:** AWS deployment + Strands ExtractorAgent + 281/821 union extractors
>
> **Description:**
>
> Per `../Design/09_Technical_Implementation_Spec.md`, the work on this branch (`feat/aws-strands-integration`) covers:
>
> 1. Strands `ExtractorAgent` on AgentCore Runtime that wraps `kernel/pipeline/extract.EXTRACTORS[union]` as `@tool`s (Design/07 §2.3)
> 2. CDK v2 (TypeScript) stacks for the 7-layer architecture (Design/09 §3-8)
> 3. Add `sprinkler_fitters_281` and `sprinkler_fitters_821` extractors + profiles using kernel's `.claude/harness` pattern
> 4. Pre-publish validation Lambdas (checksums + range; Design/09 L6)
> 5. Renderer Lambdas (xlsx + CSV + Articles; Design/09 L7)
> 6. API Gateway + Lambdas (Design/09 L2)
> 7. React admin SPA (Design/09 L1)
> 8. CloudWatch dashboards + alarms + SNS topics (Design/09 L6)
>
> Target: 2 weeks. UAT against customer's existing rate sheets in `kernel/data/<union>/ratesheet/`.
>
> Scope-down: 1 agent (Extractor only); AgentCore Memory/Gateway/Identity/Policy/Registry deferred to v1.1+. See Design/09 §15 for the full deferred-items list.
