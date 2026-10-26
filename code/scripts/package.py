"""Build ``code.zip`` for submission, and assert it is safe to upload.

Uses :mod:`zipfile` rather than a shell command so it behaves the same on
Windows, macOS and Linux, and writes forward-slash archive paths so the zip
reads correctly everywhere.

Run from the repository root::

    python code/scripts/package.py

What goes in: ``README.md`` at the repository root plus everything under
``code/``. What stays out: caches, virtual environments, secrets, archives and
the per-run JSON results. The assertions at the end are the point - a package
that smuggles in the dataset or a ``.env`` is worse than no package.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE = REPO_ROOT / "code.zip"

#: What the archive contains, relative to the repository root.
INCLUDE: tuple[str, ...] = ("README.md", "code")

#: Directory names excluded wherever they appear.
EXCLUDED_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".ruff_cache", ".cache", ".venv", "venv", ".pytest_cache", ".git"}
)

#: Exact file names excluded wherever they appear.
EXCLUDED_NAMES: frozenset[str] = frozenset({".env", ".DS_Store"})

#: File suffixes excluded wherever they appear.
EXCLUDED_SUFFIXES: frozenset[str] = frozenset(
    {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pyc"}
)

#: Reports that must be present, or the package is incomplete.
REQUIRED_MEMBERS: tuple[str, ...] = (
    "code/evaluation/evaluation_report.md",
    "code/evaluation/usage_report.md",
    "README.md",
    "code/main.py",
    "code/requirements.txt",
)

#: Nested archives are rejected outright by the submission rules.
ARCHIVE_SUFFIXES: frozenset[str] = frozenset(
    {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}
)


def is_excluded(path: Path) -> str:
    """Why a path is excluded, or an empty string if it is included."""
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return "cache or environment directory"
    if path.name in EXCLUDED_NAMES or path.name.startswith(".env"):
        return "secret"
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return f"excluded suffix {path.suffix}"
    # Per-run result JSON is reproducible output, not source. The markdown
    # summary and latest.json are kept so a reader sees the measured numbers.
    parts = path.parts
    if (
        "results" in parts
        and path.suffix == ".json"
        and path.name.startswith("results_")
    ):
        return "per-run result snapshot"
    return ""


def collect() -> list[tuple[Path, str]]:
    """Every file to pack, as ``(absolute path, archive name)``."""
    members: list[tuple[Path, str]] = []
    for entry in INCLUDE:
        root = REPO_ROOT / entry
        if not root.exists():
            continue
        if root.is_file():
            members.append((root, entry))
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(REPO_ROOT)
            if is_excluded(relative):
                continue
            members.append((path, relative.as_posix()))
    return members


def build() -> list[str]:
    """Write the archive and return its member names."""
    members = collect()
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in members:
            archive.write(path, name)
    return [name for _path, name in members]


def verify(names: list[str]) -> list[str]:
    """Check the archive against the submission rules. Returns any failures."""
    failures: list[str] = []

    dataset_members = [name for name in names if name.startswith("dataset/")]
    if dataset_members:
        failures.append(f"dataset/ is included ({len(dataset_members)} files)")

    secrets = [
        name
        for name in names
        if Path(name).name.startswith(".env") or Path(name).name == ".env"
    ]
    if secrets:
        failures.append(f"secret files included: {secrets}")

    nested = [name for name in names if Path(name).suffix.lower() in ARCHIVE_SUFFIXES]
    if nested:
        failures.append(f"nested archives included: {nested}")

    caches = [
        name
        for name in names
        if any(part in EXCLUDED_DIRS for part in Path(name).parts)
    ]
    if caches:
        failures.append(f"cache or environment files included: {caches[:5]}")

    for required in REQUIRED_MEMBERS:
        if required not in names:
            failures.append(f"missing required member: {required}")

    size = ARCHIVE.stat().st_size
    if size > 25 * 1024 * 1024:
        failures.append(f"archive is {size / 1_048_576:.1f} MB, which is too large")
    if size < 20 * 1024:
        failures.append(f"archive is only {size} bytes, which looks wrong")

    return failures


def main() -> int:
    """Build the archive, list it, and assert it is safe."""
    names = build()
    size = ARCHIVE.stat().st_size

    print(f"built {ARCHIVE.relative_to(REPO_ROOT)}  "
          f"({len(names)} files, {size / 1024:.1f} KiB)")
    print()
    by_directory: dict[str, int] = {}
    for name in names:
        parent = str(Path(name).parent).replace("\\", "/")
        by_directory[parent] = by_directory.get(parent, 0) + 1
    print("contents by directory")
    for directory, count in sorted(by_directory.items()):
        print(f"  {count:>4}  {directory}")
    print()
    print("members")
    for name in names:
        print(f"  {name}")
    print()

    failures = verify(names)
    checks = (
        ("no dataset/ included", not any(n.startswith("dataset/") for n in names)),
        ("no .env or secret files", not any(
            Path(n).name.startswith(".env") for n in names
        )),
        ("no nested archives", not any(
            Path(n).suffix.lower() in ARCHIVE_SUFFIXES for n in names
        )),
        ("no caches or venvs", not any(
            any(part in EXCLUDED_DIRS for part in Path(n).parts) for n in names
        )),
        ("evaluation_report.md present",
         "code/evaluation/evaluation_report.md" in names),
        ("usage_report.md present", "code/evaluation/usage_report.md" in names),
        ("README.md present", "README.md" in names),
        ("size reasonable", 20 * 1024 <= size <= 25 * 1024 * 1024),
    )
    print("assertions")
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    print()

    if failures:
        print("PACKAGE REJECTED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("package OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
