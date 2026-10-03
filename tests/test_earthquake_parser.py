"""Offline contracts for the side-effect-free JMA earthquake XML parser."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from docich.earthquake_parser import (
    ControlStatus,
    EarthquakeXmlError,
    Freshness,
    UpdateKind,
    classify_update,
    freshness,
    parse_jma_earthquake_xml,
)


FIXTURES = Path(__file__).parent / "fixtures" / "jma_earthquake"


def parse_fixture(name: str):
    return parse_jma_earthquake_xml((FIXTURES / name).read_bytes())


@pytest.mark.parametrize(
    "name",
    [
        "eew_forecast_cancel_serial_32.xml",
        "eew_warning_cancel.xml",
        "eew_prediction_cancel.xml",
    ],
)
def test_official_cancellations_are_explicit_and_do_not_invent_intensity(name):
    report = parse_fixture(name)

    assert report.is_cancellation
    assert report.info_type == "取消"
    assert report.event_id == "20240417231454"
    assert report.intensity_element_count == 0
    assert report.control_status_kind is ControlStatus.NORMAL


def test_training_sample_preserves_an_empty_serial_without_inference():
    report = parse_fixture("earthquake_training_empty_serial.xml")

    assert report.is_training
    assert report.control_status_kind is ControlStatus.TRAINING
    assert report.serial_element_present
    assert report.serial is None
    assert report.event_id == "20091001134500"


def test_official_test_sample_is_marked_as_test():
    report = parse_fixture("earthquake_delivery_test.xml")

    assert report.is_test
    assert report.control_status_kind is ControlStatus.TEST
    assert report.control_status == "試験"


def test_same_event_and_serial_body_revision_is_an_update_not_a_duplicate():
    original = parse_fixture("eew_forecast_serial_32.xml")
    cancellation = parse_fixture("eew_forecast_cancel_serial_32.xml")

    assert original.event_id == cancellation.event_id
    assert original.serial == cancellation.serial == "32"
    assert original.body_sha256 != cancellation.body_sha256
    assert original.document_sha256 != cancellation.document_sha256
    assert classify_update(original, cancellation) is UpdateKind.UPDATED
    assert classify_update(cancellation, original) is UpdateKind.STALE
    assert classify_update(original, parse_fixture("eew_forecast_serial_32.xml")) is UpdateKind.DUPLICATE


def test_different_report_product_is_not_joined_only_by_event_id():
    forecast = parse_fixture("eew_forecast_cancel_serial_32.xml")
    warning = parse_fixture("eew_warning_cancel.xml")

    assert forecast.event_id == warning.event_id
    assert forecast.title != warning.title
    assert classify_update(forecast, warning) is UpdateKind.NEW


def test_freshness_uses_only_explicit_report_time_and_caller_window():
    report = parse_fixture("eew_forecast_serial_32.xml")
    issued = report.report_datetime
    assert issued is not None
    window = timedelta(minutes=5)

    assert freshness(report, now=issued + timedelta(seconds=300), max_age=window) is Freshness.FRESH
    assert freshness(report, now=issued + timedelta(seconds=301), max_age=window) is Freshness.STALE
    assert freshness(report, now=issued - timedelta(seconds=1), max_age=window) is Freshness.FUTURE


def test_missing_report_time_stays_unknown():
    source = (FIXTURES / "eew_forecast_serial_32.xml").read_bytes()
    source = source.replace(
        b"<ReportDateTime>2024-04-17T23:16:58+09:00</ReportDateTime>",
        b"<ReportDateTime></ReportDateTime>",
    )
    report = parse_jma_earthquake_xml(source)

    assert report.report_datetime is None
    assert freshness(
        report,
        now=datetime(2024, 4, 17, 14, 17, tzinfo=timezone.utc),
        max_age=timedelta(minutes=5),
    ) is Freshness.UNKNOWN


@pytest.mark.parametrize(
    "now,max_age",
    [
        (datetime(2024, 4, 17, 23, 17), timedelta(minutes=5)),
        (datetime(2024, 4, 17, 14, 17, tzinfo=timezone.utc), timedelta(0)),
        (datetime(2024, 4, 17, 14, 17, tzinfo=timezone.utc), -timedelta(seconds=1)),
    ],
)
def test_freshness_rejects_naive_clock_or_nonpositive_window(now, max_age):
    report = parse_fixture("eew_forecast_serial_32.xml")
    with pytest.raises(EarthquakeXmlError):
        freshness(report, now=now, max_age=max_age)


@pytest.mark.parametrize(
    "source",
    [
        b"not XML",
        b"<Report />",
        b'<!DOCTYPE Report [<!ENTITY x "expanded">]>'
        + (FIXTURES / "eew_forecast_serial_32.xml").read_bytes(),
    ],
)
def test_malformed_wrong_root_and_dtd_inputs_fail_closed(source):
    with pytest.raises(EarthquakeXmlError):
        parse_jma_earthquake_xml(source)


def test_dtd_is_rejected_even_when_xml_uses_utf16():
    source = (FIXTURES / "eew_forecast_serial_32.xml").read_text()
    source = source[source.index("<Report"):]
    doctype = '<!DOCTYPE Report [<!ENTITY x "expanded">]>'
    source = f'<?xml version="1.0" encoding="UTF-16"?>\n{doctype}\n{source}'

    with pytest.raises(EarthquakeXmlError, match="unsafe-xml-declaration"):
        parse_jma_earthquake_xml(source.encode("utf-16"))


def test_foreign_namespace_metadata_is_not_accepted_by_local_name_alone():
    source = (FIXTURES / "earthquake_training_empty_serial.xml").read_bytes()
    source = source.replace(
        "<Title>震源に関する情報</Title>".encode(),
        '<evil:Title xmlns:evil="urn:invalid">震源に関する情報</evil:Title>'.encode(),
    )

    with pytest.raises(EarthquakeXmlError):
        parse_jma_earthquake_xml(source)


def test_non_earthquake_product_is_rejected():
    source = (FIXTURES / "earthquake_training_empty_serial.xml").read_bytes()
    source = source.replace("震源".encode(), "河川".encode())

    with pytest.raises(EarthquakeXmlError, match="not-earthquake-report"):
        parse_jma_earthquake_xml(source)


@pytest.mark.parametrize(
    "timestamp",
    ["not-a-timestamp", "2024-04-17T23:16:58"],
)
def test_invalid_or_timezone_free_report_time_is_rejected(timestamp):
    source = (FIXTURES / "eew_forecast_serial_32.xml").read_bytes()
    source = source.replace(
        b"2024-04-17T23:16:58+09:00",
        timestamp.encode(),
        1,
    )

    with pytest.raises(EarthquakeXmlError):
        parse_jma_earthquake_xml(source)
