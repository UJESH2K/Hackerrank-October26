"""Tests for resource attribution: episodes, catalog links, run assignment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from audit.attribution import (
    ATTACHMENT_CATALOG_LINK,
    DATA_CATALOG_LINK,
    Attributor,
    LinkKind,
    TimingSignature,
    catalog_links,
)
from audit.evidence.index import build_index
from audit.identity import build_resolver
from audit.loaders import Dataset
from audit.models import (
    Activity,
    AuditEntry,
    EvidenceFile,
    Identity,
    Observation,
    ReportedStatus,
    Request,
)


def at(month: int, day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    """A 2026 timestamp for fixtures."""
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


ALICE = Identity(
    user_id="user_01",
    name="Alice Doe",
    email="alice@example.com",
    phone="+44 7700 900111",
    email_hash="a" * 64,
    updated_at=at(1, 1),
)
BOB = Identity(
    user_id="user_02",
    name="Bob Roe",
    email="bob@example.com",
    phone="+44 7700 900222",
    email_hash="b" * 64,
    updated_at=at(1, 1),
)

MANIFEST = (
    EvidenceFile("evidence_01", "evidence/records/a.json", "capture", at(3, 1)),
    EvidenceFile("evidence_02", "evidence/records/b.json", "capture", at(8, 20)),
    EvidenceFile("evidence_03", "evidence/records/c.json", "capture", at(9, 1)),
)


def request(
    case_id: str,
    requested: datetime,
    reported: datetime,
    audit: datetime,
    status: ReportedStatus = ReportedStatus.COMPLETE,
    email: str = "alice@example.com",
) -> Request:
    """Build a request fixture."""
    return Request(
        case_id=case_id,
        email=email,
        requested_at=requested,
        reported_status=status,
        reported_at=reported,
        audit_at=audit,
        request_text="Please delete my account.",
    )


def observation(
    evidence_id: str,
    resource_id: str,
    observed_at: datetime,
    keys: set[str],
    system_id: str = "search_service",
) -> Observation:
    """Build an observation fixture."""
    return Observation(
        evidence_id=evidence_id,
        observed_at=observed_at,
        system_id=system_id,
        resource_id=resource_id,
        copy_label="primary",
        present=True,
        link_keys=frozenset(keys),
    )


def build(
    requests: tuple[Request, ...],
    observations: tuple[Observation, ...] = (),
    activities: tuple[Activity, ...] = (),
    audit_log: tuple[AuditEntry, ...] = (),
) -> Attributor:
    """Assemble an attributor over fixture data."""
    dataset = Dataset(
        dataset_dir=Path("dataset"),
        requests=requests,
        identities=(ALICE, BOB),
        audit_log=audit_log,
        activities=activities,
        rules=(),
        evidence_files=MANIFEST,
    )
    index = build_index(dataset, list(observations))
    return Attributor(dataset, index, build_resolver(dataset))


# -- catalog link patterns ------------------------------------------------- #


def test_data_catalog_pattern_extracts_resource_email_and_user() -> None:
    match = DATA_CATALOG_LINK.search(
        "Data catalog linked export_12 (NST-2026-0718-1842) to ingrid.dahl@outlook.com "
        "and user_18."
    )
    assert match is not None
    assert match.group("resource_id") == "export_12"
    assert match.group("email") == "ingrid.dahl@outlook.com"
    assert match.group("user_id") == "user_18"


def test_attachment_catalog_pattern_extracts_the_sole_owner() -> None:
    """This sentence is also the SUPPORT_ATTACHMENT rule's condition."""
    match = ATTACHMENT_CATALOG_LINK.search(
        "Attachment catalog confirmed object_9 (delivery_confirmation_3.pdf) belongs "
        "only to priya.raman@gmail.com and user_44; no other subject is linked."
    )
    assert match is not None
    assert match.group("resource_id") == "object_9"
    assert match.group("file") == "delivery_confirmation_3.pdf"
    assert match.group("user_id") == "user_44"


def test_catalog_links_are_timed_at_the_activity() -> None:
    activity = Activity(
        activity_id="activity_5",
        event_time=at(8, 25),
        event_type="LINK_RESOURCE_IDENTITY",
        source_system="data_catalog",
        target_system="search_service",
        resource_id="search_9",
        reported_result="SUCCESS",
        details="Data catalog linked search_9 (search_9) to alice@example.com and user_01.",
    )
    dataset = Dataset(
        dataset_dir=Path("dataset"),
        requests=(),
        identities=(ALICE,),
        audit_log=(),
        activities=(activity,),
        rules=(),
        evidence_files=(),
    )
    links = catalog_links(dataset)
    assert len(links) == 1
    assert links[0].user_id == "user_01"
    assert links[0].resource_id == "search_9"
    assert links[0].system_id == "search_service"
    assert links[0].at == at(8, 25)
    assert links[0].kind is LinkKind.CATALOG_LINK
    assert links[0].supporting_id == "activity_5"


def test_an_unrelated_activity_row_produces_no_link() -> None:
    activity = Activity(
        activity_id="activity_6",
        event_time=at(8, 25),
        event_type="DELETE_RECORD",
        source_system="deletion_worker",
        target_system="account_service",
        resource_id="account_9",
        reported_result="SUCCESS",
        details="Deletion worker submitted primary profile account_9 to account_service.",
    )
    dataset = Dataset(
        dataset_dir=Path("dataset"),
        requests=(),
        identities=(ALICE,),
        audit_log=(),
        activities=(activity,),
        rules=(),
        evidence_files=(),
    )
    assert catalog_links(dataset) == []


# -- deletion episodes ----------------------------------------------------- #


def test_a_link_from_an_earlier_episode_does_not_leak_forward() -> None:
    """The same person can file twice; February's link is not August's resource."""
    february = request("case_1", at(2, 1), at(2, 3), at(2, 10))
    august = request("case_2", at(8, 1), at(8, 3), at(8, 10))
    stale = observation("evidence_01", "search_february", at(2, 5), {"alice@example.com"})
    fresh = observation("evidence_02", "search_august", at(8, 5), {"alice@example.com"})
    attributor = build((february, august), (stale, fresh))

    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert set(attributor.linked_resources(subject, february)) == {"search_february"}
    assert set(attributor.linked_resources(subject, august)) == {"search_august"}


def test_the_episode_window_allows_seven_days_of_slack_after_audit_at() -> None:
    """Sample case_23's audit record is only linked two days after audit_at."""
    case = request("case_1", at(9, 2), at(9, 5), at(9, 15))
    late = observation(
        "evidence_03", "audit_record_1", at(9, 17), {"alice@example.com"}, "security_service"
    )
    attributor = build((case,), (late,))
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert "audit_record_1" in attributor.linked_resources(subject, case)


def test_a_link_beyond_the_slack_is_out_of_episode() -> None:
    case = request("case_1", at(9, 2), at(9, 5), at(9, 15))
    far = observation("evidence_03", "search_1", at(9, 30), {"alice@example.com"})
    attributor = build((case,), (far,))
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert attributor.linked_resources(subject, case) == {}


def test_episode_window_bounds() -> None:
    case = request("case_1", at(9, 10), at(9, 12), at(9, 20))
    attributor = build((case,))
    start, end = attributor.episode_window(case)
    assert start == at(9, 10) - timedelta(days=7)
    assert end == at(9, 20) + timedelta(days=7)


# -- ownership by someone else --------------------------------------------- #


def test_a_resource_owned_by_another_user_is_not_attributed() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    theirs = observation("evidence_02", "search_bob", at(8, 5), {"bob@example.com"})
    attributor = build((case,), (theirs,))
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert attributor.linked_resources(subject, case) == {}
    assert attributor.is_owned_by_other("search_bob", "user_01")


def test_look_alike_identifiers_do_not_link() -> None:
    """``alice@example.com`` and ``alicia@example.com`` are different people."""
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    other = observation("evidence_02", "search_x", at(8, 5), {"alicia@example.com"})
    attributor = build((case,), (other,))
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert attributor.linked_resources(subject, case) == {}


# -- the case evidence file ------------------------------------------------ #


def test_case_evidence_is_the_latest_file_at_or_before_audit_at() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 25))
    early = observation("evidence_02", "search_1", at(8, 20), {"alice@example.com"})
    late = observation("evidence_03", "search_2", at(8, 24), {"alice@example.com"})
    attributor = build((case,), (early, late))
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    # evidence_03 is captured on 9-01, after audit_at, so evidence_02 is chosen.
    assert attributor.select_case_evidence(subject, case) == "evidence_02"


def test_case_evidence_is_empty_when_nothing_is_linked() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 25))
    attributor = build((case,))
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert attributor.select_case_evidence(subject, case) == ""


# -- the timing signature -------------------------------------------------- #


def test_signature_requires_the_offset_to_be_in_range_and_on_a_band() -> None:
    """A 28-hour offset is how a wrong account candidate gives itself away."""
    signature = TimingSignature(bands=frozenset({41}), max_offset_seconds=12000.0)
    assert signature.agrees(1781)
    assert not signature.agrees(96161)
    assert not signature.agrees(-41)
    assert not signature.agrees(1780)


@pytest.mark.parametrize(
    ("offset", "cadence", "expected"),
    [
        (1781, frozenset({41}), True),
        (1781, frozenset({40}), True),
        (1781, frozenset({22, 52}), False),
        (1781, frozenset(), True),
    ],
)
def test_run_cadence_matching(offset: int, cadence: frozenset[int], expected: bool) -> None:
    """An empty cadence carries no information and must exclude nothing."""
    assert Attributor.matches_cadence(offset, cadence) is expected


# -- workflow-run account assignment --------------------------------------- #


def deletion(
    activity_id: str, resource_id: str, when: datetime, result: str = "SUCCESS"
) -> Activity:
    """A deletion-worker account deletion."""
    return Activity(
        activity_id=activity_id,
        event_time=when,
        event_type="DELETE_RECORD",
        source_system="deletion_worker",
        target_system="account_service",
        resource_id=resource_id,
        reported_result=result,
        details=f"Deletion worker submitted primary profile {resource_id} to account_service.",
    )


def test_the_result_must_match_the_reported_status() -> None:
    """A COMPLETE claim is backed by SUCCESS, not by FAILED."""
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    activities = (
        deletion("activity_1", "account_good", at(8, 2), "SUCCESS"),
        deletion("activity_2", "account_bad", at(8, 2), "FAILED"),
    )
    attributor = build((case,), activities=activities)
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    scores = {
        candidate.resource_id: candidate.score
        for candidate in attributor.account_candidates(case, subject)
    }
    assert scores["account_good"] > scores["account_bad"]


def test_a_deletion_outside_the_run_window_is_not_a_candidate() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    activities = (
        deletion("activity_1", "account_in", at(8, 2)),
        deletion("activity_2", "account_before", at(7, 30)),
        deletion("activity_3", "account_after", at(8, 4)),
    )
    attributor = build((case,), activities=activities)
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert {
        candidate.resource_id for candidate in attributor.account_candidates(case, subject)
    } == {"account_in"}


def test_an_account_owned_by_another_user_is_not_a_candidate() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    theirs = observation(
        "evidence_02", "account_bob", at(8, 2), {"bob@example.com"}, "account_service"
    )
    attributor = build(
        (case,),
        observations=(theirs,),
        activities=(deletion("activity_1", "account_bob", at(8, 2)),),
    )
    subject = attributor.resolver.resolve("alice@example.com")
    assert subject is not None
    assert attributor.account_candidates(case, subject) == ()


def test_joint_assignment_never_gives_one_account_to_two_cases() -> None:
    """Two cases, two accounts: each must take the one it scores higher on."""
    first = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    second = request(
        "case_2", at(8, 1), at(8, 3), at(8, 10), ReportedStatus.NOT_STARTED
    )
    activities = (
        deletion("activity_1", "account_x", at(8, 3), "SUCCESS"),
        deletion("activity_2", "account_y", at(8, 3), "FAILED"),
    )
    attributor = build((first, second), activities=activities)
    attributions = attributor.attribute_all(allow_fallback=False)
    assert attributions["case_1"].account_resource_id == "account_x"
    assert attributions["case_2"].account_resource_id == "account_y"


def test_an_unbreakable_tie_is_left_unassigned_without_the_fallback() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    activities = (
        deletion("activity_1", "account_x", at(8, 3), "SUCCESS"),
        deletion("activity_2", "account_y", at(8, 3), "SUCCESS"),
    )
    attributor = build((case,), activities=activities)
    attribution = attributor.attribute_all(allow_fallback=False)["case_1"]
    assert attribution.account_resource_id == ""
    assert any("unassigned" in note for note in attribution.notes)


def test_the_fallback_picks_a_plausible_account_and_flags_it() -> None:
    """A plausible id beats an empty verification, but must be marked as such."""
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    activities = (
        deletion("activity_1", "account_x", at(8, 3), "SUCCESS"),
        deletion("activity_2", "account_y", at(8, 3), "SUCCESS"),
    )
    attributor = build((case,), activities=activities)
    attribution = attributor.attribute_all(allow_fallback=True)["case_1"]
    assert attribution.account_resource_id != ""
    account = next(
        resource
        for resource in attribution.resources
        if resource.resource_id.startswith("account_")
    )
    assert account.how is LinkKind.WORKFLOW_RUN
    assert account.confidence < 0.5
    assert any("fallback" in note for note in attribution.notes)


def test_an_evidence_linked_account_is_never_overridden_by_the_workflow() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    linked = observation(
        "evidence_02", "account_real", at(8, 2), {"alice@example.com"}, "account_service"
    )
    attributor = build(
        (case,),
        observations=(linked,),
        activities=(deletion("activity_1", "account_decoy", at(8, 3)),),
    )
    attribution = attributor.attribute_all()["case_1"]
    assert attribution.account_resource_id == "account_real"
    account = next(
        resource
        for resource in attribution.resources
        if resource.resource_id.startswith("account_")
    )
    assert account.how is LinkKind.EVIDENCE_LINK
    assert account.confidence == 1.0


# -- the public shape ------------------------------------------------------ #


def test_attributed_resources_are_sorted_and_carry_their_reason() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    observations = (
        observation("evidence_02", "search_9", at(8, 2), {"alice@example.com"}),
        observation(
            "evidence_02", "account_1", at(8, 2), {"alice@example.com"}, "account_service"
        ),
    )
    attributor = build((case,), observations)
    resources = attributor.attribute_all()["case_1"].resources
    assert [resource.resource_id for resource in resources] == ["account_1", "search_9"]
    assert all(resource.how is LinkKind.EVIDENCE_LINK for resource in resources)
    assert all(resource.supporting_ids == ("evidence_02",) for resource in resources)


def test_attribute_reads_from_the_joint_result() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10))
    linked = observation("evidence_02", "search_9", at(8, 2), {"alice@example.com"})
    attributor = build((case,), (linked,))
    assert [resource.resource_id for resource in attributor.attribute("case_1")] == [
        "search_9"
    ]


def test_an_unresolvable_email_yields_no_resources() -> None:
    case = request("case_1", at(8, 1), at(8, 3), at(8, 10), email="nobody@example.com")
    attribution = build((case,)).attribute_all()["case_1"]
    assert attribution.subject is None
    assert attribution.resources == ()
    assert any("did not resolve" in note for note in attribution.notes)
