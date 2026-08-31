"""Assemble the Hugging Face Space folder for the NEXUS backend.

Copies `backend/` into a staging directory, swaps in the Space README (frontmatter with
`sdk: docker`), and bakes production env defaults into the Dockerfile (LLM off, CORS for the
Vercel frontend, modest worker count). Upload the result with:

    uvx --from huggingface_hub hf upload <user>/nexus <staging-dir> . --repo-type space
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", ".hypothesis"}

ENV_BLOCK = """
# ---- Hugging Face Space runtime defaults (override in Space settings) --------------------------
ENV NEXUS_LLM_ENABLED=false \\
    NEXUS_DECISION_WORKERS=2 \\
    NEXUS_LIVE_TICKS_PER_SECOND=10 \\
    NEXUS_DEFAULT_SCALE=small \\
    NEXUS_LOG_JSON=true \\
    NEXUS_CORS_ORIGIN_REGEX="https?://(localhost|127[.]0[.]0[.]1)(:[0-9]+)?|https://[a-z0-9.-]+[.]vercel[.]app"
"""


def main(out: Path) -> int:
    if out.exists():
        shutil.rmtree(out)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in EXCLUDE_DIRS}

    shutil.copytree(ROOT / "backend", out, ignore=ignore)
    shutil.copyfile(ROOT / "deploy" / "hf-space" / "README.md", out / "README.md")
    dockerfile = out / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    marker = 'ENV PATH="/opt/venv/bin:$PATH"'
    if marker not in text:
        raise SystemExit("Dockerfile marker not found; update deploy/build_space.py")
    text = text.replace(marker, marker + "\n" + ENV_BLOCK)
    dockerfile.write_text(text, encoding="utf-8", newline="\n")
    files = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"space staged at {out} ({files} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "nexus-space-staging"))
