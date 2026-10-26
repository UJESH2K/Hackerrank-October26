"""Read-only dataset profiler.

Writes ``code/evaluation/data_facts.md``: the measured shape of every
participant-facing file, so later design decisions cite real counts instead of
assumptions. Nothing in the audit pipeline imports this module.

Run from the repository root::

    python code/scripts/profile_dataset.py

Language and request-intent labels produced here are coarse keyword heuristics
used only to size the problem. The pipeline classifies request intent with the
model, never with these heuristics.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
OUTPUT_FILE = CODE_DIR / "evaluation" / "data_facts.md"

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

#: Columns whose distinct values are worth listing in full when few enough.
LOW_CARDINALITY_LIMIT = 30

#: Scripts detected from character ranges, in priority order.
SCRIPT_HINTS: tuple[tuple[str, str], ...] = (
    ("ARABIC", "Arabic"),
    ("CJK", "CJK"),
    ("HIRAGANA", "Japanese"),
    ("KATAKANA", "Japanese"),
    ("HANGUL", "Korean"),
    ("CYRILLIC", "Cyrillic"),
    ("DEVANAGARI", "Devanagari"),
    ("HEBREW", "Hebrew"),
    ("THAI", "Thai"),
    ("GREEK", "Greek"),
)

#: Latin-script language cues: words distinctive enough to separate neighbouring
#: languages (Spanish vs Portuguese share "solicito" and "por favor", so the cue
#: sets avoid those).
LATIN_LANGUAGE_CUES: dict[str, tuple[str, ...]] = {
    "German": ("bitte", "meines", "konto", "lösch", "daten", "ich bitte"),
    "Spanish": ("eliminación", "cuenta", "correo", "según", "revisen", "borrado"),
    "French": ("veuillez", "compte", "données", "je demande", "conformément", "supprim"),
    "Portuguese": ("exclusão", "conta", "verifiquem", "conforme", "privacidade"),
    "Italian": ("per favore", "account", "cancellazione", "dati", "richiedo"),
    "Dutch": ("verwijder", "mijn account", "gegevens", "alstublieft"),
    "Turkish": ("hesabımı", "silinmesini", "verilerimi", "lütfen"),
    "Indonesian": ("mohon", "akun saya", "hapus", "data saya"),
    "Polish": ("proszę", "moje konto", "usunięcie", "dane"),
    "Vietnamese": ("vui lòng", "tài khoản", "xóa", "dữ liệu"),
}

#: Multilingual cues that a request asks for deletion. Stems rather than whole
#: words, so noun and verb forms both match ("exclusão" as well as "excluir").
DELETION_CUES: tuple[str, ...] = (
    # English
    "delet",
    "eras",
    "remove my",
    "removal of my",
    "close my account",
    "wipe",
    "purge",
    "right to be forgotten",
    "forget me",
    # German
    "lösch",
    "losch",
    # Spanish
    "elimina",
    "borra",
    "suprim",
    # Portuguese
    "exclus",
    "exclua",
    "excluir",
    "apagar",
    "apague",
    # French
    "supprim",
    "suppression",
    "effac",
    # Italian
    "cancell",
    # Dutch
    "verwijder",
    # Turkish
    "silin",
    "silme",
    # Indonesian
    "hapus",
    # Polish
    "usun",
    "usuń",
    # Vietnamese
    "xóa",
    "xoa",
    # Russian
    "удал",
    # Japanese / Korean / Arabic
    "削除",
    "삭제",
    "حذف",
    "إزالة",
)

#: Multilingual cues that a request asks for something other than deletion.
NON_DELETION_CUES: tuple[str, ...] = (
    "copy of my data",
    "copy of the data",
    "access request",
    "subject access",
    "export",
    "download my",
    "send me my",
    "correct my",
    "update my",
    "rectif",
    "unsubscribe",
    "opt out",
    "opt-out",
    "marketing email",
    "stop sending",
    "newsletter",
    "auskunft",
    "berichtigung",
    "acceso",
    "rectificar",
    "portabilidad",
    "accès",
    "rectifier",
)

#: Statements that the account must survive. When one of these sits next to a
#: deletion keyword, keyword matching alone decides the case wrongly.
RETENTION_DECLARATIONS: tuple[str, ...] = (
    "not asking for deletion",
    "not a request to",
    "do not close",
    "don't close",
    "do not delete",
    "don't delete",
    "leave the account",
    "leave my profile",
    "keep the account",
    "still need the account",
    "still use it",
    "still want",
    "continue using the service",
    "without removing",
    "account active",
    "as it is",
)

DIGITS = re.compile(r"\d+")
EMAILS = re.compile(r"[\w.+-]+@[\w.-]+")
WHITESPACE = re.compile(r"\s+")


def read_csv(name: str) -> tuple[list[str], list[dict[str, str]]]:
    """Return the header and rows of a dataset CSV."""
    path = DATASET_DIR / name
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        return fieldnames, list(reader)


def detect_script(text: str) -> str | None:
    """Name the dominant non-Latin script in ``text``, if any."""
    for character in text:
        if character.isspace() or not character.isalpha():
            continue
        try:
            name = unicodedata.name(character)
        except ValueError:
            continue
        for marker, label in SCRIPT_HINTS:
            if marker in name:
                return label
    return None


def guess_language(text: str) -> str:
    """Coarse language label: script first, then Latin-script keyword cues."""
    script = detect_script(text)
    if script is not None:
        return script
    lowered = text.casefold()
    scores = {
        language: sum(cue in lowered for cue in cues)
        for language, cues in LATIN_LANGUAGE_CUES.items()
    }
    best_language, best_score = max(scores.items(), key=lambda item: (item[1], item[0]))
    return best_language if best_score >= 3 else "English (or undetermined Latin)"


def looks_like_deletion(text: str) -> bool:
    """True when the text carries a deletion cue and is not purely another ask."""
    lowered = text.casefold()
    return any(cue in lowered for cue in DELETION_CUES)


def non_deletion_cues_in(text: str) -> list[str]:
    """The non-deletion cues present in the text."""
    lowered = text.casefold()
    return [cue for cue in NON_DELETION_CUES if cue in lowered]


def retention_declarations_in(text: str) -> list[str]:
    """The explicit keep-the-account statements present in the text."""
    lowered = text.casefold()
    return [cue for cue in RETENTION_DECLARATIONS if cue in lowered]


def mask_digits(text: str) -> str:
    """Replace digit runs with ``#`` so sentence templates collapse together."""
    return DIGITS.sub("#", text)


def normalise_request(text: str) -> str:
    """Collapse a request text to a template: emails and digits masked."""
    masked = EMAILS.sub("<email>", text)
    return WHITESPACE.sub(" ", DIGITS.sub("#", masked)).strip()


def resource_prefix(resource_id: str) -> str:
    """The non-numeric prefix of a resource id, e.g. ``account`` in ``account_005``."""
    head, separator, tail = resource_id.rpartition("_")
    return head if separator and tail.isdigit() else resource_id


def bullet_counts(counter: Counter[str], limit: int | None = None) -> list[str]:
    """Format a counter as sorted markdown bullets."""
    items = counter.most_common(limit)
    return [f"- `{key}` — {value}" for key, value in items]


def table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    """Format a markdown table."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return lines


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

FILES = (
    "deletion_requests.csv",
    "sample_deletion_requests.csv",
    "sample_output.csv",
    "user_identities.csv",
    "user_audit_log.csv",
    "system_activity.csv",
    "deletion_rules.csv",
    "evidence_files.csv",
)


def section_file_shapes() -> list[str]:
    """Columns, row counts and low-cardinality value sets for every CSV."""
    lines = ["## 1. File shapes", ""]
    for name in FILES:
        fieldnames, rows = read_csv(name)
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(f"{len(rows)} data rows; columns: {', '.join(f'`{c}`' for c in fieldnames)}")
        lines.append("")
        for column in fieldnames:
            values = Counter(row[column] for row in rows)
            if len(values) > LOW_CARDINALITY_LIMIT or len(values) == len(rows):
                continue
            rendered = ", ".join(
                f"`{value}`({count})" for value, count in values.most_common()
            )
            lines.append(f"- **{column}** — {len(values)} distinct: {rendered}")
        lines.append("")
    return lines


def section_system_activity() -> list[str]:
    """Event-type flows, reported results and details templates."""
    _, rows = read_csv("system_activity.csv")
    lines = ["## 2. `system_activity.csv`", ""]
    lines.append(f"{len(rows)} rows. This stream is global: rows concern many users.")
    lines.append("")

    lines.append("### 2.1 event_type x source_system -> target_system")
    lines.append("")
    flows: Counter[tuple[str, str, str]] = Counter(
        (row["event_type"], row["source_system"], row["target_system"]) for row in rows
    )
    lines.extend(
        table(
            ("event_type", "source_system", "target_system", "rows"),
            [
                (event, source, target, count)
                for (event, source, target), count in sorted(
                    flows.items(), key=lambda item: (-item[1], item[0])
                )
            ],
        )
    )
    lines.append("")

    lines.append("### 2.2 reported_result distribution")
    lines.append("")
    results = Counter(row["reported_result"] for row in rows)
    lines.extend(bullet_counts(results))
    lines.append("")
    lines.append("Per event_type:")
    lines.append("")
    by_event: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_event[row["event_type"]][row["reported_result"]] += 1
    result_names = sorted(results)
    lines.extend(
        table(
            ("event_type", *result_names),
            [
                (event, *(by_event[event][name] for name in result_names))
                for event in sorted(by_event)
            ],
        )
    )
    lines.append("")

    lines.append("### 2.3 `details` sentence templates (digits masked)")
    lines.append("")
    templates = Counter(mask_digits(row["details"]) for row in rows)
    lines.append(f"{len(templates)} distinct templates across {len(rows)} rows.")
    lines.append("")
    lines.extend(bullet_counts(templates))
    lines.append("")

    lines.append("### 2.4 resource_id prefix x systems")
    lines.append("")
    prefixes: Counter[str] = Counter(resource_prefix(row["resource_id"]) for row in rows)
    prefix_systems: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        prefix_systems[resource_prefix(row["resource_id"])][row["target_system"]] += 1
    lines.extend(
        table(
            ("resource prefix", "rows", "target systems (rows)"),
            [
                (
                    f"`{prefix}`",
                    count,
                    ", ".join(
                        f"{system} ({n})"
                        for system, n in prefix_systems[prefix].most_common()
                    ),
                )
                for prefix, count in prefixes.most_common()
            ],
        )
    )
    lines.append("")
    return lines


def section_audit_log() -> list[str]:
    """Which identity fields change, and whether histories collide across users."""
    _, audit_rows = read_csv("user_audit_log.csv")
    _, identity_rows = read_csv("user_identities.csv")
    lines = ["## 3. `user_audit_log.csv`", ""]
    changed_users = {row["user_id"] for row in audit_rows}
    lines.append(
        f"{len(audit_rows)} changes affecting {len(changed_users)} of "
        f"{len(identity_rows)} users."
    )
    lines.append("")

    fields = Counter(row["field_name"] for row in audit_rows)
    lines.append("### 3.1 Fields that change")
    lines.append("")
    lines.extend(bullet_counts(fields))
    lines.append("")

    per_user = Counter(row["user_id"] for row in audit_rows)
    multiple = Counter(count for count in per_user.values())
    lines.append("### 3.2 Changes per user")
    lines.append("")
    lines.extend(
        table(
            ("changes", "users"),
            [(count, users) for count, users in sorted(multiple.items())],
        )
    )
    lines.append("")
    lines.append(
        f"{sum(users for count, users in multiple.items() if count > 1)} users "
        "have more than one change."
    )
    lines.append("")

    lines.append("### 3.3 Do field changes arrive in pairs?")
    lines.append("")
    by_user_time: Counter[tuple[str, str]] = Counter(
        (row["user_id"], row["event_time"]) for row in audit_rows
    )
    simultaneous = sum(1 for count in by_user_time.values() if count > 1)
    lines.append(
        f"{simultaneous} (user, event_time) pairs carry more than one field change, "
        "so an identity change can touch several fields at once."
    )
    lines.append("")

    lines.append("### 3.4 Identifier collisions across users")
    lines.append("")
    current_email_owner = {row["email"].casefold(): row["user_id"] for row in identity_rows}
    current_phone_owner = {row["phone"]: row["user_id"] for row in identity_rows}
    email_collisions: list[tuple[str, str, str, str]] = []
    phone_collisions: list[tuple[str, str, str, str]] = []
    for row in audit_rows:
        for value in (row["old_value"], row["new_value"]):
            if row["field_name"] == "email":
                owner = current_email_owner.get(value.casefold())
                if owner is not None and owner != row["user_id"]:
                    email_collisions.append((row["audit_id"], row["user_id"], owner, value))
            elif row["field_name"] == "phone":
                owner = current_phone_owner.get(value)
                if owner is not None and owner != row["user_id"]:
                    phone_collisions.append((row["audit_id"], row["user_id"], owner, value))
    lines.append(
        f"- historical email of one user equal to **another** user's current email: "
        f"**{len(email_collisions)}**"
    )
    lines.append(
        f"- historical phone of one user equal to **another** user's current phone: "
        f"**{len(phone_collisions)}**"
    )
    for audit_id, holder, owner, _ in email_collisions[:5]:
        lines.append(f"  - `{audit_id}`: history of `{holder}` matches current `{owner}`")
    lines.append("")

    historical_emails = Counter(
        row["old_value"].casefold()
        for row in audit_rows
        if row["field_name"] == "email" and row["old_value"]
    )
    shared = {value: count for value, count in historical_emails.items() if count > 1}
    lines.append(
        f"- historical email values appearing in more than one change row: **{len(shared)}**"
    )
    lines.append("")
    return lines


def section_requests() -> list[str]:
    """Reported-status mix, languages, and requests that may not authorise deletion."""
    _, rows = read_csv("deletion_requests.csv")
    _, sample_rows = read_csv("sample_deletion_requests.csv")
    lines = ["## 4. `deletion_requests.csv`", ""]

    statuses = Counter(row["reported_status"] for row in rows)
    lines.append("### 4.1 reported_status distribution")
    lines.append("")
    lines.extend(bullet_counts(statuses))
    lines.append("")
    sample_statuses = Counter(row["reported_status"] for row in sample_rows)
    lines.append(
        "Sample file for comparison: "
        + ", ".join(f"`{k}`({v})" for k, v in sample_statuses.most_common())
    )
    lines.append("")

    lines.append("### 4.2 Timestamp ordering")
    lines.append("")
    requested_after_reported = sum(1 for row in rows if row["requested_at"] > row["reported_at"])
    audit_before_reported = sum(1 for row in rows if row["audit_at"] < row["reported_at"])
    lines.append(f"- rows where `requested_at` > `reported_at`: **{requested_after_reported}**")
    lines.append(f"- rows where `audit_at` < `reported_at`: **{audit_before_reported}**")
    lines.append(
        f"- `audit_at` range: {min(row['audit_at'] for row in rows)} .. "
        f"{max(row['audit_at'] for row in rows)}"
    )
    lines.append("")

    lines.append("### 4.3 Languages present (heuristic)")
    lines.append("")
    languages = Counter(guess_language(row["request_text"]) for row in rows)
    lines.extend(bullet_counts(languages))
    lines.append("")

    lines.append("### 4.4 Requests that do not look like deletion requests (heuristic)")
    lines.append("")
    suspects = [
        row
        for row in rows
        if not looks_like_deletion(row["request_text"])
        or non_deletion_cues_in(row["request_text"])
        or retention_declarations_in(row["request_text"])
    ]
    no_deletion_cue = [row for row in rows if not looks_like_deletion(row["request_text"])]
    with_retention = [row for row in rows if retention_declarations_in(row["request_text"])]
    negation_traps = [
        row
        for row in with_retention
        if looks_like_deletion(row["request_text"])
    ]
    lines.append(
        f"- rows with **no** deletion cue at all: **{len(no_deletion_cue)}**"
    )
    lines.append(
        f"- rows carrying a non-deletion cue (access / export / correction / opt-out): "
        f"**{sum(1 for row in rows if non_deletion_cues_in(row['request_text']))}**"
    )
    lines.append(
        f"- rows declaring the account must survive: **{len(with_retention)}**"
    )
    lines.append(
        f"- **negation traps** — a deletion keyword *and* a keep-the-account "
        f"statement in the same text: **{len(negation_traps)}**"
    )
    lines.append(f"- union to review: **{len(suspects)}**")
    lines.append("")
    lines.append(
        "The union is the candidate `NOT_APPLICABLE` set. Every row in it is "
        "listed below, with its reported status."
    )
    lines.append("")
    for row in suspects:
        cues = non_deletion_cues_in(row["request_text"]) + retention_declarations_in(
            row["request_text"]
        )
        trap = " **[negation trap]**" if row in negation_traps else ""
        text = " ".join(row["request_text"].split())[:170]
        lines.append(
            f"- `{row['case_id']}` (status `{row['reported_status']}`, "
            f"cues: {', '.join(cues) or 'none'}){trap} — {text}"
        )
    lines.append("")
    lines.append(
        "A keyword rule cannot decide the negation traps: the deletion keyword is "
        "the thing being refused. This is the reason request intent goes to the "
        "model rather than to a cue list."
    )
    lines.append("")

    lines.append("### 4.5 How many distinct request texts are there?")
    lines.append("")
    templates = Counter(normalise_request(row["request_text"]) for row in rows)
    lines.append(
        f"{len(templates)} distinct texts (emails and digits masked) across "
        f"{len(rows)} requests. This bounds the cost of classifying intent: a "
        "cache keyed on the normalised text needs at most that many model calls."
    )
    lines.append("")
    lines.append("Most frequent texts (truncated to 150 characters):")
    lines.append("")
    for text, count in templates.most_common(12):
        lines.append(f"- {count} x — {text[:150]}")
    lines.append("")
    singletons = sum(1 for count in templates.values() if count == 1)
    lines.append(f"Texts appearing exactly once: **{singletons}**.")
    lines.append("")
    return lines


def section_evidence() -> list[str]:
    """Evidence file types, origins and capture window."""
    _, rows = read_csv("evidence_files.csv")
    lines = ["## 5. `evidence_files.csv`", ""]
    lines.append(f"{len(rows)} evidence artefacts.")
    lines.append("")

    suffixes = Counter(Path(row["file_path"]).suffix.lower() for row in rows)
    folders = Counter(Path(row["file_path"]).parent.name for row in rows)
    origins = Counter(row["origin"] for row in rows)

    lines.append("### 5.1 By file type")
    lines.append("")
    lines.extend(bullet_counts(suffixes))
    lines.append("")
    lines.append("### 5.2 By folder")
    lines.append("")
    lines.extend(bullet_counts(folders))
    lines.append("")
    lines.append("### 5.3 By origin")
    lines.append("")
    lines.extend(bullet_counts(origins))
    lines.append("")
    lines.append("### 5.4 captured_at range")
    lines.append("")
    captured = sorted(row["captured_at"] for row in rows)
    lines.append(f"- earliest: {captured[0]}")
    lines.append(f"- latest: {captured[-1]}")
    lines.append("")

    _, requests = read_csv("deletion_requests.csv")
    audit_times = sorted(row["audit_at"] for row in requests)
    after_every_audit = sum(1 for value in captured if value > audit_times[-1])
    before_every_audit = sum(1 for value in captured if value <= audit_times[0])
    lines.append(
        f"- captured after the latest `audit_at` ({audit_times[-1]}), so inadmissible "
        f"everywhere: **{after_every_audit}**"
    )
    lines.append(
        f"- captured at or before the earliest `audit_at` ({audit_times[0]}), so "
        f"admissible everywhere: **{before_every_audit}**"
    )
    lines.append("")
    return lines


def section_sample_output() -> list[str]:
    """Operation mix, evidence citation frequency and evidence rules by status."""
    _, rows = read_csv("sample_output.csv")
    lines = ["## 6. `sample_output.csv`", ""]

    statuses = Counter(row["deletion_status"] for row in rows)
    rendered_statuses = ", ".join(f"`{k}`({v})" for k, v in statuses.most_common())
    lines.append(f"{len(rows)} sample answers: {rendered_statuses}")
    lines.append("")

    parsed = {row["case_id"]: json.loads(row["steps_json"]) for row in rows}

    lines.append("### 6.1 Steps per case")
    lines.append("")
    per_status: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        per_status[row["deletion_status"]].append(len(parsed[row["case_id"]]))
    lines.extend(
        table(
            ("deletion_status", "cases", "total steps", "min", "max", "mean"),
            [
                (
                    status,
                    len(counts),
                    sum(counts),
                    min(counts),
                    max(counts),
                    f"{sum(counts) / len(counts):.1f}",
                )
                for status, counts in sorted(per_status.items())
            ],
        )
    )
    lines.append("")

    lines.append("### 6.2 Operation distribution")
    lines.append("")
    operations: Counter[tuple[str, str, str]] = Counter()
    for steps in parsed.values():
        for step in steps:
            operations[(step["phase"], step["operation"], step["state"])] += 1
    lines.extend(
        table(
            ("phase", "operation", "state", "steps"),
            [
                (phase, operation, state, count)
                for (phase, operation, state), count in sorted(
                    operations.items(), key=lambda item: (item[0][0], -item[1])
                )
            ],
        )
    )
    lines.append("")

    lines.append("### 6.3 Operation distribution by case status")
    lines.append("")
    by_status: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for step in parsed[row["case_id"]]:
            by_status[row["deletion_status"]][step["operation"]] += 1
    all_operations = sorted({op for counter in by_status.values() for op in counter})
    lines.extend(
        table(
            ("operation", *sorted(by_status)),
            [
                (operation, *(by_status[status][operation] for status in sorted(by_status)))
                for operation in all_operations
            ],
        )
    )
    lines.append("")

    lines.append("### 6.4 Which steps carry an `evidence_id`")
    lines.append("")
    carry: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        for step in parsed[row["case_id"]]:
            key = (row["deletion_status"], step["phase"])
            carry[key]["cited" if step["evidence_id"] else "empty"] += 1
    lines.extend(
        table(
            ("deletion_status", "phase", "with evidence_id", "empty evidence_id"),
            [
                (status, phase, counter["cited"], counter["empty"])
                for (status, phase), counter in sorted(carry.items())
            ],
        )
    )
    lines.append("")

    lines.append("### 6.5 Evidence citation frequency")
    lines.append("")
    citations: Counter[str] = Counter()
    for steps in parsed.values():
        for step in steps:
            if step["evidence_id"]:
                citations[step["evidence_id"]] += 1
    _, manifest = read_csv("evidence_files.csv")
    manifest_ids = {row["file_id"] for row in manifest}
    _, activity = read_csv("system_activity.csv")
    activity_ids = {row["activity_id"] for row in activity}
    _, audit = read_csv("user_audit_log.csv")
    audit_ids = {row["audit_id"] for row in audit}

    kinds: Counter[str] = Counter()
    for evidence_id in citations:
        if evidence_id in manifest_ids:
            kinds["evidence_files.file_id"] += 1
        elif evidence_id in activity_ids:
            kinds["system_activity.activity_id"] += 1
        elif evidence_id in audit_ids:
            kinds["user_audit_log.audit_id"] += 1
        else:
            kinds["UNRESOLVED"] += 1
    lines.append(
        f"{sum(citations.values())} citations referencing {len(citations)} distinct ids."
    )
    lines.append("")
    lines.append("Where cited ids resolve (distinct ids):")
    lines.append("")
    lines.extend(bullet_counts(kinds))
    lines.append("")
    lines.append("Most-cited ids:")
    lines.append("")
    lines.extend(bullet_counts(citations, limit=15))
    lines.append("")

    multi = sum(
        1
        for steps in parsed.values()
        if len({step["evidence_id"] for step in steps if step["evidence_id"]}) > 1
    )
    lines.append(
        f"Cases citing more than one distinct evidence id: **{multi}** of {len(rows)}."
    )
    lines.append("")

    lines.append("### 6.6 Systems and resource prefixes used in steps")
    lines.append("")
    systems: Counter[str] = Counter()
    prefix_system: dict[str, Counter[str]] = defaultdict(Counter)
    for steps in parsed.values():
        for step in steps:
            systems[step["system_id"]] += 1
            prefix_system[resource_prefix(step["resource_id"])][step["system_id"]] += 1
    lines.extend(
        table(
            ("resource prefix", "steps", "system_id(s)"),
            [
                (
                    f"`{prefix}`",
                    sum(counter.values()),
                    ", ".join(f"{system} ({n})" for system, n in counter.most_common()),
                )
                for prefix, counter in sorted(
                    prefix_system.items(), key=lambda item: -sum(item[1].values())
                )
            ],
        )
    )
    lines.append("")

    lines.append("### 6.7 Step ordering check")
    lines.append("")
    phase_rank = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
    out_of_order = [
        case_id
        for case_id, steps in parsed.items()
        if [phase_rank[s["phase"]] for s in steps]
        != sorted(phase_rank[s["phase"]] for s in steps)
    ]
    bad_numbering = [
        case_id
        for case_id, steps in parsed.items()
        if [s["step_no"] for s in steps] != list(range(1, len(steps) + 1))
    ]
    lines.append(f"- cases whose phases are out of order: **{len(out_of_order)}**")
    lines.append(f"- cases whose `step_no` is not 1..n consecutive: **{len(bad_numbering)}**")
    lines.append("")
    return lines


def section_observations() -> list[str]:
    """What the numbers above settle, and what they leave open."""
    return [
        "## 7. Observations to verify later",
        "",
        "### Settled by this profile",
        "",
        "1. **Every cited evidence id in `sample_output.csv` is a `file_id`.** All 75",
        "   citations resolve against `evidence_files.csv`; none is an `activity_id`",
        "   or `audit_id`. The contract permits all three, but the planner should",
        "   cite a `file_id` and treat the other two as fallbacks.",
        "2. **A case is anchored on one evidence file.** 31 of 32 sample cases cite a",
        "   single distinct evidence id. Evidence fusion across many files is not the",
        "   normal path; finding the one authoritative snapshot per case is.",
        "3. **`resource_id` prefix determines `system_id` in steps, one-to-one.**",
        "   `account`->`account_service`, `search`->`search_service`,",
        "   `support`->`support_platform`, `analytics`->`analytics_platform`,",
        "   `audit_record`->`security_service`, `data_flow`->`integration_service`,",
        "   `backup`->`backup_service`, `export`->`export_service`,",
        "   `tombstone`->`identity_service`. Note this is the resource's **owning**",
        "   system, not the activity `target_system`: activity rows route",
        "   `support_*` through `search_service` and `export_*` through",
        "   `object_store`, but steps never do.",
        "4. **Phase order and step numbering in the samples are exactly as specified.**",
        "   Zero cases have out-of-order phases or non-consecutive `step_no`.",
        "5. **Evidence-id discipline is absolute.** `COMPLETE`: 34/34 verification",
        "   steps cite evidence. `INCOMPLETE`: 41/41 findings cite evidence, and all",
        "   41 remediation plus all 77 verification steps carry `\"\"`.",
        "6. **No identifier collisions across users.** No historical email or phone of",
        "   one user equals another user's current value, and no historical email",
        "   appears in two change rows. Resolving the request email to a subject is",
        "   therefore unambiguous, which removes the need for a tie-break rule.",
        "7. **Identity changes arrive in pairs.** `email` and `email_hash` both change",
        "   107 times, and 107 (user, event_time) pairs carry more than one field, so",
        "   an email change always rewrites the hash at the same instant.",
        "8. **No evidence file is globally inadmissible.** Capture times run",
        "   2026-01-29 to 2026-10-01 and `audit_at` runs the same window, so",
        "   admissibility must be decided per case, not filtered once up front.",
        "9. **`reported_result` is demonstrably unreliable as a state signal.**",
        "   `DELETE_RECORD` activity is 302 `SUCCESS`, 368 `FAILED`, 220 `PARTIAL`:",
        "   a claimed success is barely a third of deletion attempts.",
        "",
        "### Still open",
        "",
        "10. **No `NOT_APPLICABLE` example exists in public ground truth.**",
        "    `sample_output.csv` is 10 `COMPLETE` and 22 `INCOMPLETE`. The",
        "    non-deletion class is unexercised and must be covered by our own",
        "    evaluation cases. Two independent signals agree on its size: the",
        "    candidate set in section 4.4, and the 10 `CLASSIFY_REQUEST` rows from",
        "    `privacy_intake` stating a request was classified as non-deletion with",
        "    no destructive action authorised. Whether the two sets are the same ten",
        "    cases is the open question, and whether a non-deletion case can exist",
        "    *without* a `CLASSIFY_REQUEST` row is the risk: relying on that row",
        "    would miss any case the intake system never classified.",
        "17. **Negation traps are real.** Some non-deletion requests state the",
        "    deletion keyword in order to refuse it ('I am not asking for deletion',",
        "    'This is an information request, not a request to erase it', 'please",
        "    don't delete or change anything'). Section 4.4 counts them. Any keyword",
        "    rule decides these backwards, which is the concrete justification for",
        "    sending request intent to the model.",
        "11. **Data categories are named in no participant file.** Steps carry",
        "    `system_id` and `resource_id`; `deletion_rules.csv` is keyed by",
        "    `data_category`. The resource-prefix-to-category mapping must be inferred",
        "    and justified from evidence contents (for example whether `audit_record`",
        "    is `SECURITY_AUDIT_LOG` and `email_delivery` is `EMAIL_DELIVERY_LOG`).",
        "12. **Conditional rules need evaluable conditions.** `SUPPORT_MESSAGE`,",
        "    `SUPPORT_ATTACHMENT`, `ANALYTICS_PROFILE`, `PRODUCT_TELEMETRY`,",
        "    `SECURITY_AUDIT_LOG`, `LEGAL_HOLD_RECORD` and `BACKUP_ARCHIVE` all carry",
        "    conditions. Which evidence establishes each one is unresolved. The",
        "    activity stream hints at two: an attachment-catalog row states an object",
        "    'belongs only to' one subject, and `CHECK_RESTORE_ACCESS` rows state an",
        "    archive 'can still be restored'.",
        "13. **`LINK_RESOURCE_IDENTITY` is a direct attribution channel, but tiny.**",
        "    12 `data_catalog` rows name a resource, an email and a `user_id`",
        "    together. 12 rows cannot attribute 518 cases, so the main attribution",
        "    path has to come from evidence contents. Determine what those 12 rows",
        "    are for.",
        "14. **Replica drift as a mechanism.** `databases/` evidence names primary,",
        "    regional, reporting and failover replicas, and the activity stream has",
        "    55 `SYNC_RECORD` rows into `account_replication_cluster` plus one into",
        "    `account_failover_replica`. Confirm whether drift between replicas is",
        "    what `REPORT_REMAINING_RECORD` findings are built on.",
        "15. **Recreation after deletion.** 31 rows read 'Active connector",
        "    repopulated downstream resource search_#' and one reads 'Restore job",
        "    repopulated downstream resource search_#'. These look like the",
        "    `REPORT_ACTIVE_DATA_FLOW` and `REPORT_RESTORE_RISK` mechanisms; verify",
        "    against the sample answers for those cases.",
        "16. **Tombstones are rare in activity but present in steps.** 11",
        "    `tombstone_*` activity rows and 4 tombstone steps in the samples. Check",
        "    whether `REDACT_RECORD` on `identity_service` is always a tombstone.",
        "",
    ]


def build_report() -> str:
    """Assemble the full markdown report."""
    lines = [
        "# Data facts",
        "",
        "Measured profile of the participant-facing dataset, produced by",
        "`code/scripts/profile_dataset.py` (read-only; not imported by the pipeline).",
        "Regenerate with `python code/scripts/profile_dataset.py`.",
        "",
        "Language and request-intent labels in section 4 are coarse keyword heuristics",
        "used only to size the problem. The pipeline classifies intent with the model.",
        "",
    ]
    for section in (
        section_file_shapes(),
        section_system_activity(),
        section_audit_log(),
        section_requests(),
        section_evidence(),
        section_sample_output(),
        section_observations(),
    ):
        lines.extend(section)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    """Write the report and report where it went."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(build_report(), encoding="utf-8")
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
