"""Tests for the log parser: every line style, and trace grouping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from audit.evidence.context import ParseContext
from audit.evidence.log_parser import parse_line, parse_log, parse_logfmt

CAPTURED_AT = datetime(2026, 9, 26, 10, 38, 56, tzinfo=UTC)


def context_for(tmp_path: Path, body: str, name: str = "service.log") -> ParseContext:
    """Write a fixture and return the context that parses it."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return ParseContext(path=path, evidence_id="evidence_test", captured_at=CAPTURED_AT)


# -- logfmt tokenising ----------------------------------------------------- #


def test_logfmt_handles_quoted_values_with_spaces() -> None:
    pairs, bare = parse_logfmt('message_excerpt="Customer asked about an order update."')
    assert pairs == {"message_excerpt": "Customer asked about an order update."}
    assert bare == []


def test_logfmt_handles_escaped_quotes() -> None:
    pairs, _ = parse_logfmt('message="instance=\\"collector-1\\" event=\\"export_started\\""')
    assert pairs["message"] == 'instance="collector-1" event="export_started"'


def test_logfmt_handles_bare_and_unquoted_values() -> None:
    pairs, bare = parse_logfmt("INFO service=search_service rows=0")
    assert pairs == {"service": "search_service", "rows": "0"}
    assert bare == ["INFO"]


# -- one test per line style ----------------------------------------------- #


def test_bracketed_style() -> None:
    record = parse_line(
        '2026-02-22T10:58:51Z [DEBUG] [account_service] [tr-9071194] '
        'profile_lookup_completed: instance="account-1" resource_id="account_7023" '
        'rows="1" http_status="200"'
    )
    assert record is not None
    assert record.service == "account_service"
    assert record.trace == "tr-9071194"
    assert record.event == "profile_lookup_completed"
    assert record.resource_id == "account_7023"


def test_cloud_runtime_style_unwraps_the_message_payload() -> None:
    record = parse_line(
        '2026-06-08T06:52:09Z requestId=tr-1108860 level=INFO function=search_service '
        'message="instance=\\"search-3\\" resource_id=\\"search_021\\" '
        'event=\\"index_query_completed\\" result=\\"hit\\" '
        'correlation_key=\\"mrios@example.com\\""'
    )
    assert record is not None
    assert record.trace == "tr-1108860"
    assert record.service == "search_service"
    assert record.event == "index_query_completed"
    assert record.fields["result"] == "hit"
    assert record.fields["correlation_key"] == "mrios@example.com"


def test_record_lookup_style_is_one_line_per_record() -> None:
    record = parse_line(
        '2026-03-15T07:00:00Z INFO service=downstream_delivery_service '
        'event=record_lookup seq=1 system="account_service" resource_id="account_022" '
        'record_type="USER_PROFILE" result="NOT_FOUND" '
        'subject_reference="zofia@example.com"'
    )
    assert record is not None
    assert record.trace == ""
    assert record.fields["result"] == "NOT_FOUND"
    assert record.fields["record_type"] == "USER_PROFILE"


def test_pipe_separated_style() -> None:
    record = parse_line(
        '2026-07-03T07:51:32Z | DEBUG | security_service | tr-5156005 | '
        'audit_record_read_completed | instance="security-6" '
        'resource_id="audit_record_4413" rows="1" correlation_key="user_211"'
    )
    assert record is not None
    assert record.service == "security_service"
    assert record.trace == "tr-5156005"
    assert record.event == "audit_record_read_completed"
    assert record.resource_id == "audit_record_4413"


def test_unquoted_logfmt_style() -> None:
    record = parse_line(
        "2026-02-18T05:47:12Z level=INFO service=integration_service "
        "trace_id=rb-054712-001 event=connector_config_read_completed "
        "resource_id=data_flow_3669 enabled=true customer_reference=null"
    )
    assert record is not None
    assert record.trace == "rb-054712-001"
    assert record.fields["enabled"] == "true"
    assert "customer_reference" not in record.fields


def test_json_payload_style() -> None:
    record = parse_line(
        '2026-04-19T06:51:21Z {"severity":"INFO","service":"search_service",'
        '"trace_id":"tr-7722221","resource_id":"search_9365",'
        '"event":"index_query_completed","result":"hit"}'
    )
    assert record is not None
    assert record.service == "search_service"
    assert record.trace == "tr-7722221"
    assert record.resource_id == "search_9365"


def test_syslog_style() -> None:
    record = parse_line(
        '2026-03-25T08:51:54Z <14>1 node-4 account_service tr-3269055 - - level=INFO '
        'instance="account-6" resource_id="account_7606" event="profile_lookup_completed" '
        'rows="0" http_status="404" correlation_key="storres@example.com"'
    )
    assert record is not None
    assert record.service == "account_service"
    assert record.trace == "tr-3269055"
    assert record.fields["http_status"] == "404"


def test_a_line_without_a_timestamp_is_not_a_record() -> None:
    assert parse_line("this is not a log line") is None


# -- trace grouping -------------------------------------------------------- #


def test_trace_grouping_joins_the_lookup_key_to_the_result(tmp_path: Path) -> None:
    """The started line holds the identity; the completed line holds the state."""
    body = (
        '2026-02-22T10:58:45Z [DEBUG] [account_service] [tr-9071194] '
        'profile_lookup_started: lookup_key="isabela@example.com" attempt="1"\n'
        '2026-02-22T10:58:51Z [DEBUG] [account_service] [tr-9071194] '
        'profile_lookup_completed: resource_id="account_7023" rows="1" '
        'http_status="200" duration_ms="59"\n'
    )
    result = parse_log(context_for(tmp_path, body))
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.resource_id == "account_7023"
    assert observation.present is True
    assert observation.link_keys == {"isabela@example.com"}
    assert observation.copy_label == "trace"
    assert observation.observed_at == datetime(2026, 2, 22, 10, 58, 51, tzinfo=UTC)


def test_a_404_trace_proves_absence_and_keeps_its_lookup_key(tmp_path: Path) -> None:
    body = (
        '2026-03-25T08:51:50Z <15>1 node-4 account_service tr-3269055 - - '
        'event="profile_lookup_started" correlation_key="storres@example.com"\n'
        '2026-03-25T08:51:54Z <14>1 node-4 account_service tr-3269055 - - '
        'resource_id="account_7606" event="profile_lookup_completed" rows="0" '
        'http_status="404"\n'
    )
    observation = parse_log(context_for(tmp_path, body)).observations[0]
    assert observation.present is False
    assert observation.link_keys == {"storres@example.com"}


def test_one_trace_touching_two_resources_yields_two_observations(tmp_path: Path) -> None:
    body = (
        "2026-02-18T05:47:12Z level=DEBUG service=account_service trace_id=t-1 "
        "event=lookup_started lookup_key=a@b.com\n"
        "2026-02-18T05:47:13Z level=INFO service=account_service trace_id=t-1 "
        "event=lookup_completed resource_id=account_1 rows=1\n"
        "2026-02-18T05:47:14Z level=INFO service=search_service trace_id=t-1 "
        "event=index_query_completed resource_id=search_1 result=miss\n"
    )
    result = parse_log(context_for(tmp_path, body))
    found = {item.resource_id: item for item in result.observations}
    assert set(found) == {"account_1", "search_1"}
    assert found["account_1"].present is True
    assert found["search_1"].present is False
    # Both inherit the trace's identity link.
    assert found["search_1"].link_keys == {"a@b.com"}
    # Each is attributed to its own system, not the trace's first service.
    assert found["search_1"].system_id == "search_service"


def test_several_lines_about_one_resource_merge_into_one_observation(
    tmp_path: Path,
) -> None:
    body = (
        "2026-09-17T22:00:02Z level=DEBUG service=support_replica trace_id=tr-rp-002 "
        "event=contact_query_started lookup_key=nadia@example.com\n"
        "2026-09-17T22:00:05Z level=INFO service=support_replica trace_id=tr-rp-002 "
        "event=contact_query_completed resource_id=support_002 rows=1\n"
        "2026-09-17T22:00:06Z level=WARN service=support_replica trace_id=tr-rp-002 "
        "event=deletion_marker_check resource_id=support_002 marker_found=false "
        "last_write=2026-09-07T10:01:00Z\n"
    )
    result = parse_log(context_for(tmp_path, body))
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.present is True
    assert observation.attributes["marker_found"] == "false"
    assert observation.link_keys == {"nadia@example.com"}


def test_infrastructure_noise_is_dropped(tmp_path: Path) -> None:
    """Lines with no resource and no identity are not evidence about anyone."""
    body = (
        '2026-02-25T15:46:59Z [DEBUG] [thread_pool] [app-8] pool_sample: '
        'instance="app-8" active="30" queued="2"\n'
        '2026-02-24T01:22:52Z [ERROR] [webhook_delivery] [notify-8] delivery_abandoned: '
        'destination="status_page" attempts="5" http_status="503"\n'
        '2026-02-27T06:11:06Z [INFO] [runtime_config] [app-8] properties_reloaded: '
        'instance="app-8" changed="2"\n'
    )
    result = parse_log(context_for(tmp_path, body))
    assert result.observations == []
    assert result.warnings == []


def test_unparsable_lines_are_reported(tmp_path: Path) -> None:
    result = parse_log(context_for(tmp_path, "not a log line at all\n"))
    assert result.observations == []
    assert len(result.warnings) == 1
    assert "unparsed line" in result.warnings[0]


@pytest.mark.parametrize(
    ("result_value", "expected"),
    [("hit", True), ("miss", False), ("FOUND", True), ("NOT_FOUND", False)],
)
def test_result_vocabulary_drives_presence(
    tmp_path: Path, result_value: str, expected: bool
) -> None:
    body = (
        "2026-09-15T08:51:57Z level=INFO service=search_service trace_id=tr-1 "
        f"resource_id=search_1874 event=index_query_completed result={result_value}\n"
    )
    assert parse_log(context_for(tmp_path, body)).observations[0].present is expected


def test_a_pii_scan_proves_the_record_exists_and_still_exposes_an_identifier(
    tmp_path: Path,
) -> None:
    """This line shape is the entire basis of a REPORT_EXPOSED_PII finding."""
    body = (
        '2026-03-25T08:51:23Z <12>1 node-1 support_platform tr-7458912 - - level=WARN '
        'resource_id="support_9464" event="message_pii_scan" '
        'matched_value="silva.mariana@icloud.com" detector="direct_identifier"\n'
    )
    observation = parse_log(context_for(tmp_path, body)).observations[0]
    assert observation.present is True
    assert observation.attributes["detector"] == "direct_identifier"
    assert observation.link_keys == {"silva.mariana@icloud.com"}


def test_a_connector_heartbeat_proves_an_active_data_flow(tmp_path: Path) -> None:
    """And this shape is the basis of a REPORT_ACTIVE_DATA_FLOW finding."""
    body = (
        "2026-01-29T11:00:00Z level=WARN service=integration_service trace_id=tr-023 "
        "event=connector_heartbeat resource_id=data_flow_1121 enabled=true "
        "scheduler_registered=true subject_key=jain.aditi@gmail.com\n"
    )
    observation = parse_log(context_for(tmp_path, body)).observations[0]
    assert observation.present is True
    assert observation.attributes["enabled"] == "true"
    assert observation.system_id == "integration_service"


def test_a_started_line_alone_proves_nothing(tmp_path: Path) -> None:
    """``analytics_profile_read_started`` must not be read as a completed read."""
    body = (
        "2026-07-03T07:51:49Z | DEBUG | analytics_platform | tr-486 | "
        "analytics_profile_scan_started | resource_id=\"analytics_257\" attempt=\"1\"\n"
    )
    observation = parse_log(context_for(tmp_path, body)).observations[0]
    assert observation.present is None


def test_a_trace_records_its_whole_event_sequence(tmp_path: Path) -> None:
    body = (
        "2026-09-17T22:00:05Z level=INFO service=support_replica trace_id=tr-1 "
        "event=contact_query_completed resource_id=support_002 rows=1\n"
        "2026-09-17T22:00:06Z level=WARN service=support_replica trace_id=tr-1 "
        "event=deletion_marker_check resource_id=support_002 marker_found=false\n"
    )
    observation = parse_log(context_for(tmp_path, body)).observations[0]
    assert observation.attributes["events"] == (
        "contact_query_completed,deletion_marker_check"
    )
    assert observation.attributes["event"] == "deletion_marker_check"
