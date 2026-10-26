"""Command-line entry point for the deletion-audit agent.

Run from the repository root::

    python code/main.py                                   # audit all 518 cases
    python code/main.py --no-llm                           # structured data only
    python code/main.py --cases case_33,case_34            # audit named cases
    python code/main.py --input dataset/sample_deletion_requests.csv \
        --output /tmp/sample_output.csv

The run writes ``output.csv`` and then validates it against the submission
contract, so a contract violation fails here rather than at grading time.

Model calls need ``ANTHROPIC_API_KEY`` in the environment. Without it, or with
``--no-llm``, the run still completes using the deterministic pipeline alone.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = Path(__file__).resolve().parent

# Allow ``python code/main.py`` to import the ``audit`` package.
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from audit.classifier import Decider  # noqa: E402  (after sys.path setup)
from audit.llm import LlmGateway, LlmSettings  # noqa: E402
from audit.loaders import Dataset, load_dataset  # noqa: E402
from audit.models import DeletionStatus  # noqa: E402
from audit.pipeline import audit, load_learning_requests, write_rows  # noqa: E402
from audit.validate import validate_file  # noqa: E402

DEFAULT_CONFIG_FILE = CODE_DIR / "config.yaml"


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved run settings: config file values with CLI overrides applied."""

    config: dict[str, Any]
    dataset_dir: Path
    requests_file: Path
    output_file: Path
    cache_dir: Path
    prompts_dir: Path
    usage_run_file: Path
    use_llm: bool
    case_ids: tuple[str, ...]

    @property
    def llm_settings(self) -> LlmSettings:
        """Model ids, limits and prices."""
        return LlmSettings.from_config(self.config)

    @property
    def fallback_is_deletion(self) -> bool:
        """What an unclassifiable request becomes with no model available."""
        return bool(self.config.get("features", {}).get("fallback_is_deletion", True))


def load_config(path: Path = DEFAULT_CONFIG_FILE) -> dict[str, Any]:
    """Read ``config.yaml``."""
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python code/main.py",
        description="Audit whether each deletion request was actually completed.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="requests CSV to audit (default: dataset/deletion_requests.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where to write the predictions (default: output.csv at the repo root)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="decide every case from structured data only; make no model calls",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="comma-separated case ids to audit instead of the whole file",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help="path to config.yaml",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the validation verdict",
    )
    return parser


def _resolve(path: Path | str) -> Path:
    """Resolve a possibly-relative path against the repository root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def resolve_settings(argv: list[str] | None = None) -> Settings:
    """Parse arguments and merge them with ``config.yaml``."""
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    paths = config.get("paths", {})
    features = config.get("features", {})

    case_ids = tuple(
        case_id.strip() for case_id in args.cases.split(",") if case_id.strip()
    )
    return Settings(
        config=config,
        dataset_dir=_resolve(paths.get("dataset_dir", "dataset")),
        requests_file=_resolve(
            args.input or paths.get("requests_file", "dataset/deletion_requests.csv")
        ),
        output_file=_resolve(args.output or paths.get("output_file", "output.csv")),
        cache_dir=_resolve(paths.get("cache_dir", "code/.cache")),
        prompts_dir=_resolve(paths.get("prompts_dir", "code/prompts")),
        usage_run_file=_resolve(
            paths.get("usage_run_file", "code/evaluation/usage_run.json")
        ),
        use_llm=not args.no_llm
        and bool(
            features.get("use_llm_classifier", True)
            or features.get("use_llm_vision", True)
        ),
        case_ids=case_ids,
    )


def describe_inputs(dataset: Dataset, settings: Settings) -> str:
    """Summarise what was loaded, for operator feedback on a run."""
    selected = (
        dataset.select(settings.case_ids) if settings.case_ids else dataset.requests
    )
    lines = [
        f"requests        {len(dataset.requests):>5}  ({settings.requests_file.name})",
        f"selected        {len(selected):>5}",
        f"identities      {len(dataset.identities):>5}",
        f"audit log       {len(dataset.audit_log):>5}",
        f"activities      {len(dataset.activities):>5}",
        f"policy rules    {len(dataset.rules):>5}",
        f"evidence files  {len(dataset.evidence_files):>5}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Audit the requests, write the output, validate it. Returns an exit code."""
    args = build_parser().parse_args(argv)
    settings = resolve_settings(argv)
    dataset = load_dataset(settings.dataset_dir, settings.requests_file)
    requests = (
        dataset.select(settings.case_ids) if settings.case_ids else dataset.requests
    )

    llm_settings = settings.llm_settings
    # ``require_real_client`` is what keeps the test stand-in out of a production
    # run: a substituted client is refused outright, so this entry point either
    # calls the real API or runs the deterministic path (decision D37).
    gateway = LlmGateway(
        llm_settings,
        cache_dir=settings.cache_dir / "llm",
        enabled=settings.use_llm,
        require_real_client=True,
    )

    if not args.quiet:
        print(describe_inputs(dataset, settings))
        if gateway.available:
            print("llm             enabled (live API)")
            print(f"  classifier    {llm_settings.classifier_model}")
            print(f"  vision        {llm_settings.vision_model}")
        else:
            reason = gateway.unavailable_reason()
            print(f"llm             DISABLED - {reason}")
            print(
                "                running the deterministic pipeline only. "
                "Request intent is not classified and documents are not read, "
                "so non-deletion requests are audited as deletions."
            )
        print()

    run = audit(
        dataset,
        cache_dir=settings.cache_dir,
        learning_requests=load_learning_requests(
            settings.dataset_dir, settings.requests_file
        )
        or None,
        requests=requests,
        gateway=gateway,
        prompts_dir=settings.prompts_dir,
        fallback_is_deletion=settings.fallback_is_deletion,
    )
    write_rows(settings.output_file, run.rows())
    if run.usage.calls:
        run.usage.append_run(
            settings.usage_run_file,
            llm_settings,
            len(requests),
            errors=gateway.errors,
            auth_failed=gateway.auth_failed,
        )

    if not args.quiet:
        statuses = dict.fromkeys(DeletionStatus, 0)
        for result in run.results:
            statuses[result.deletion_status] += 1
        steps = sum(len(result.steps) for result in run.results)
        print(f"observations    {run.observations:>5}")
        print(f"  documents     {run.document_observations:>5}")
        print(f"coverage facts  {run.coverage:>5}")
        print(f"parse warnings  {len(run.warnings):>5}")
        for status, count in statuses.items():
            print(f"{str(status):<15} {count:>5}")
        print(f"steps           {steps:>5}")

        if run.decisions:
            deciders = dict.fromkeys(Decider, 0)
            for decision in run.decisions.values():
                deciders[decision.decided_by] += 1
            print()
            print("request intent")
            for decider, count in deciders.items():
                print(f"  {str(decider):<10} {count:>5}")

        usage = run.usage
        if usage.calls:
            print()
            print("model usage")
            print(f"  run type      {'cold' if usage.is_cold else 'warm'}")
            print(f"  calls         {usage.calls:>5}  ({usage.api_calls} to the API)")
            print(f"  cache hits    {usage.cache_hits:>5}")
            print(f"  input tokens  {usage.input_tokens:>5}")
            print(f"  output tokens {usage.output_tokens:>5}")
            print(f"  cost estimate ${usage.cost(llm_settings):.4f}")

        if gateway.errors:
            print()
            print("!" * 70)
            print("MODEL CALLS FAILED - this run used the deterministic path only.")
            if gateway.auth_failed:
                print(
                    "The API rejected the credentials. Request intent was not "
                    "classified and documents were not read, so non-deletion "
                    "requests were audited as deletions."
                )
            for error in gateway.errors[:5]:
                print(f"  {error}")
            print("!" * 70)

        if run.unproven_deletions:
            print()
            print(
                f"ATTRIBUTION FAILURE: {len(run.unproven_deletions)} cases classified "
                "as deletion requests have nothing to verify"
            )
            for case_id in run.unproven_deletions[:20]:
                print(f"  {case_id}")

        print(f"wrote           {settings.output_file}")
        print()

    expected = 518
    if settings.case_ids or settings.requests_file.name != "deletion_requests.csv":
        expected = len(requests)
    report = validate_file(settings.output_file, expected_rows=expected)
    print(report.summary())
    if not args.quiet:
        for error in report.errors[:20]:
            print(f"  error:   {error}")
        for warning in report.warnings[:20]:
            print(f"  warning: {warning}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
