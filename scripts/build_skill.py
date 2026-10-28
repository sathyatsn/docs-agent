"""Package the release-tickets skill as a self-contained zip for upload to Claude.

  python scripts/build_skill.py            -> dist/release-tickets.zip

The zip holds SKILL.md, the helper script, the release library and the catalog,
so it runs in Claude's sandbox without this repo. Rebuild and re-upload it after
changing config/release_catalog.yaml or src/release/.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "release-tickets"
OUT = ROOT / "dist" / "release-tickets.zip"
LIBRARY = ["__init__.py", "auditor.py", "catalog.py", "grounding.py", "jira_reader.py",
           "models.py", "planner.py", "render.py"]


def main() -> int:
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SKILL / "SKILL.md", "release-tickets/SKILL.md")
        z.write(SKILL / "scripts" / "release_plan.py", "release-tickets/scripts/release_plan.py")
        z.writestr("release-tickets/lib/src/__init__.py", "")
        for name in LIBRARY:
            z.write(ROOT / "src" / "release" / name, f"release-tickets/lib/src/release/{name}")
        z.write(ROOT / "config" / "release_catalog.yaml", "release-tickets/lib/config/release_catalog.yaml")
    print(f"Wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
