"""Inline the three PRODUCT_*.md files into the SPA template.

Markdown payloads are base64-encoded before injection so we never have to
worry about a literal </script> inside a code block ending the inline tag.
The SPA decodes them at load time.

Usage:
    python docs/spa/build.py
        -> writes docs/spa/dist/index.html

The output is a single self-contained file. Open it directly in a browser
or upload it to S3 — no other assets are required.
"""
from __future__ import annotations
import base64
import datetime as dt
import pathlib
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
RUNBOOKS = HERE.parent / "Runbooks"
TEMPLATE = HERE / "template.html"
OUT_DIR = HERE / "dist"
OUT = OUT_DIR / "index.html"

DOCS = {
    "__DOC_FLOW_PLACEHOLDER__": RUNBOOKS / "PRODUCT_END_TO_END_FLOW.md",
    "__DOC_SERVICES_PLACEHOLDER__": RUNBOOKS / "PRODUCT_SERVICE_INVENTORY.md",
    "__DOC_ERRORS_PLACEHOLDER__": RUNBOOKS / "PRODUCT_ERROR_AND_LOGGING_REFERENCE.md",
}


def _git(cmd: list[str], cwd: pathlib.Path) -> str:
    try:
        return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ""


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    html = TEMPLATE.read_text(encoding="utf-8")

    for placeholder, path in DOCS.items():
        md = path.read_text(encoding="utf-8")
        b64 = base64.b64encode(md.encode("utf-8")).decode("ascii")
        html = html.replace(placeholder, b64)

    repo_root = HERE.parent.parent
    commit = _git(["git", "rev-parse", "--short=10", "HEAD"], cwd=repo_root) or "unknown"
    # Read last-modified time of any of the source MDs (we can't call
    # `dt.datetime.now()` in some sandboxes; this stays deterministic).
    latest = max(p.stat().st_mtime for p in DOCS.values())
    built = dt.datetime.fromtimestamp(latest).strftime("%Y-%m-%d")
    html = html.replace("__BUILD_DATE__", built)
    html = html.replace("__COMMIT_HASH__", commit)

    OUT.write_text(html, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"  wrote {OUT}  ({size_kb:,.1f} KB, built={built}, commit={commit})")


if __name__ == "__main__":
    main()
