"""Package the release-tickets skill so it runs in Claude without this repo.

  python scripts/build_skill.py                      -> dist/release-tickets.zip (upload as a skill)
  python scripts/build_skill.py --plugin-dir DIR     -> also a plugin folder for the org plugin library

Both hold SKILL.md, the helper script, the release library and the catalog.
Rebuild and re-publish after changing config/release_catalog.yaml or src/release/.
"""

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "release-tickets"
OUT = ROOT / "dist" / "release-tickets.zip"
LIBRARY = ["__init__.py", "auditor.py", "catalog.py", "grounding.py", "jira_reader.py",
           "models.py", "planner.py", "render.py"]


PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = ("Create Couchbase Mobile release tickets in Jira (release epic + child tasks) "
                      "from a one-line request, via the Atlassian connector.")


def skill_files() -> dict[str, Path | str]:
    """Path inside the skill folder -> source file (or literal content)."""
    files: dict[str, Path | str] = {
        "SKILL.md": SKILL / "SKILL.md",
        "scripts/release_plan.py": SKILL / "scripts" / "release_plan.py",
        "lib/src/__init__.py": "",
        "lib/config/release_catalog.yaml": ROOT / "config" / "release_catalog.yaml",
    }
    for name in LIBRARY:
        files[f"lib/src/release/{name}"] = ROOT / "src" / "release" / name
    return files


def build_zip() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, src in skill_files().items():
            if isinstance(src, Path):
                z.write(src, f"release-tickets/{rel}")
            else:
                z.writestr(f"release-tickets/{rel}", src)
    print(f"Wrote {OUT.relative_to(ROOT)}")


def build_plugin(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    skill_dir = target / "skills" / "release-tickets"
    for rel, src in skill_files().items():
        dest = skill_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, Path):
            shutil.copyfile(src, dest)
        else:
            dest.write_text(src)
    manifest = {"name": "release-tickets", "version": PLUGIN_VERSION, "description": PLUGIN_DESCRIPTION}
    (target / ".claude-plugin").mkdir()
    (target / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote plugin folder {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin-dir", type=Path, help="Also write a plugin folder here (outside the repo)")
    args = parser.parse_args()
    build_zip()
    if args.plugin_dir:
        build_plugin(args.plugin_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
