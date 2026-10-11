"""Offline contracts for the side-effect-free JMA earthquake XML parser."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from docich.earthquake_parser import (
    ControlStatus,
    EarthquakeXmlError,
    Freshness,
    IntensityState,
    ObservationAvailability,
    ObservedProduct,
    UpdateKind,
    classify_update,
    extract_jma_observed_intensity_xml,
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


NS = {
    "j": "http://xml.kishou.go.jp/jmaxml1/",
    "h": "http://xml.kishou.go.jp/jmaxml1/informationBasis1/",
    "s": "http://xml.kishou.go.jp/jmaxml1/body/seismology1/",
}
OBS = "s:Body/s:Intensity/s:Observation"
PREF = OBS + "/s:Pref"
AREA = PREF + "/s:Area"
DETAIL = "observed_earthquake_intensity.xml"
BULLETIN = "observed_intensity_bulletin.xml"


def extract_fixture(name):
    return extract_jma_observed_intensity_xml((FIXTURES / name).read_bytes())


def variant(name, change):
    """Derived negative cases; the checked-in official XML stays unmodified."""
    root = ET.fromstring((FIXTURES / name).read_bytes())
    change(root)
    return ET.tostring(root, encoding="utf-8")


def set_text(root, path, text):
    element = root.find(path, NS)
    assert element is not None
    element.text = text


def remove(root, path):
    parent_path, _, tag = path.rpartition("/")
    parent = root.find(parent_path, NS)
    assert parent is not None
    element = parent.find(tag, NS)
    assert element is not None
    parent.remove(element)


def test_official_bulletin_preserves_prefecture_area_codes_and_split_intensities():
    result = extract_fixture(BULLETIN)

    assert result.product is ObservedProduct.INTENSITY_BULLETIN
    assert result.availability is ObservationAvailability.PRESENT
    assert result.max_intensity.code == "6-"
    assert result.report.serial is None
    pref = result.prefectures[0]
    assert (pref.code_type, pref.code, pref.name, pref.max_intensity.code) == (
        "地震情報／都道府県等", "22", "静岡県", "6-",
    )
    assert [(a.code, a.name, a.max_intensity.code) for a in pref.areas] == [
        ("442", "静岡県中部", "6-"),
        ("443", "静岡県西部", "6-"),
        ("440", "静岡県伊豆", "5+"),
        ("441", "静岡県東部", "5+"),
    ]
    assert all(a.code_type == "地震情報／細分区域" for a in pref.areas)


def test_official_detail_extracts_area_values_without_flattening_city_or_station_codes():
    result = extract_fixture(DETAIL)

    assert result.product is ObservedProduct.EARTHQUAKE_INTENSITY
    assert result.max_intensity.code == "4"
    pref = result.prefectures[0]
    assert (pref.code, pref.name) == ("46", "鹿児島県")
    assert [(a.code, a.max_intensity.code) for a in pref.areas] == [
        ("771", "4"), ("776", "4"), ("770", "3"),
        ("777", "3"), ("774", "1"), ("775", "1"), ("778", "1"),
    ]
    assert not any(a.code == "4620300" for p in result.prefectures for a in p.areas)


def test_official_prefecture_code_keeps_its_leading_zero():
    result = extract_fixture("observed_intensity_leading_zero.xml")
    assert (result.prefectures[0].code, result.prefectures[0].name) == ("03", "岩手県")


def test_official_observation_cancellation_keeps_envelope_and_has_no_observation_values():
    result = extract_fixture("observed_intensity_cancel.xml")
    assert result.availability is ObservationAvailability.CANCELLED
    assert result.report.is_cancellation
    assert result.report.serial is None
    assert result.max_intensity is None
    assert result.prefectures == ()


@pytest.mark.parametrize("status,kind", [
    ("訓練", ControlStatus.TRAINING),
    ("試験", ControlStatus.TEST),
    ("未対応の状態", ControlStatus.UNKNOWN),
])
def test_observation_rows_retain_training_test_or_unknown_provenance(status, kind):
    result = extract_jma_observed_intensity_xml(variant(
        DETAIL, lambda root: set_text(root, "j:Control/j:Status", status),
    ))
    assert result.report.control_status_kind is kind
    assert result.report.control_status == status
    assert result.report.control_status_kind is not ControlStatus.NORMAL
    assert result.prefectures == extract_fixture(DETAIL).prefectures


@pytest.mark.parametrize("path", [OBS + "/s:MaxInt", PREF + "/s:MaxInt", AREA + "/s:MaxInt"])
def test_missing_maxint_is_not_filled_from_parent_child_or_headline(path):
    result = extract_jma_observed_intensity_xml(variant(DETAIL, lambda root: remove(root, path)))
    value = {
        OBS + "/s:MaxInt": result.max_intensity,
        PREF + "/s:MaxInt": result.prefectures[0].max_intensity,
        AREA + "/s:MaxInt": result.prefectures[0].areas[0].max_intensity,
    }[path]
    assert value.state is IntensityState.MISSING
    assert value.raw is None and value.code is None


def test_empty_maxint_is_missing_without_inheriting_the_prefecture_value():
    result = extract_jma_observed_intensity_xml(variant(
        DETAIL, lambda root: set_text(root, AREA + "/s:MaxInt", " "),
    ))
    assert result.prefectures[0].areas[0].max_intensity.state is IntensityState.MISSING
    assert result.prefectures[0].max_intensity.code == "4"


def test_revision_text_is_kept_at_its_original_prefecture_and_area_scope():
    def revise(root):
        ET.SubElement(root.find(PREF, NS), "{" + NS["s"] + "}Revise").text = "上方修正"
        ET.SubElement(root.find(AREA, NS), "{" + NS["s"] + "}Revise").text = "追加"
    result = extract_jma_observed_intensity_xml(variant(DETAIL, revise))
    assert result.prefectures[0].revise == "上方修正"
    assert result.prefectures[0].areas[0].revise == "追加"


@pytest.mark.parametrize("value", ["不明", "NaN", "0", "8", "5弱", "5.0", "震度５弱以上未入電"])
def test_unknown_intensity_keeps_literal_value_and_never_becomes_a_known_code(value):
    result = extract_jma_observed_intensity_xml(variant(
        DETAIL, lambda root: set_text(root, AREA + "/s:MaxInt", value),
    ))
    intensity = result.prefectures[0].areas[0].max_intensity
    assert intensity.state is IntensityState.UNKNOWN
    assert intensity.raw == value
    assert intensity.code is None


@pytest.mark.parametrize("fixture,state", [(BULLETIN, IntensityState.UNKNOWN), (DETAIL, IntensityState.KNOWN)])
@pytest.mark.parametrize("value", ["1", "2"])
def test_known_intensity_domain_follows_the_specific_product(fixture, state, value):
    result = extract_jma_observed_intensity_xml(variant(
        fixture, lambda root: set_text(root, AREA + "/s:MaxInt", value),
    ))
    assert result.prefectures[0].areas[0].max_intensity.state is state


@pytest.mark.parametrize("change", [
    lambda root: remove(root, "s:Body/s:Intensity"),
    lambda root: setattr(root.find(OBS, NS), "tag", "{" + NS["s"] + "}Forecast"),
    lambda root: setattr(root.find(OBS, NS), "tag", "{urn:foreign}Observation"),
])
def test_absent_or_forecast_or_foreign_observation_does_not_use_other_xml_values(change):
    result = extract_jma_observed_intensity_xml(variant(DETAIL, change))
    assert result.availability is ObservationAvailability.MISSING
    assert result.prefectures == () and result.max_intensity is None


@pytest.mark.parametrize("path,value", [
    ("h:Head/h:InfoKindVersion", "2.0_0"),
    ("h:Head/h:InfoKind", "震源速報"),
    ("h:Head/h:Title", "遠地地震に関する情報"),
    ("h:Head/h:InfoType", "未対応の形態"),
])
def test_out_of_scope_product_or_version_or_info_type_is_explicitly_unsupported(path, value):
    result = extract_jma_observed_intensity_xml(variant(DETAIL, lambda root: set_text(root, path, value)))
    assert result.availability is ObservationAvailability.UNSUPPORTED
    assert result.prefectures == () and result.max_intensity is None


@pytest.mark.parametrize("fixture", [
    "eew_forecast_serial_32.xml", "earthquake_training_empty_serial.xml", "earthquake_delivery_test.xml",
])
def test_existing_nonobservation_products_keep_their_envelope_without_observed_values(fixture):
    result = extract_fixture(fixture)
    assert result.report == parse_fixture(fixture)
    assert result.availability is ObservationAvailability.UNSUPPORTED
    assert result.prefectures == ()


def test_cancellation_cannot_promote_leftover_observation_values():
    result = extract_jma_observed_intensity_xml(variant(
        DETAIL, lambda root: set_text(root, "h:Head/h:InfoType", "取消"),
    ))
    assert result.report.intensity_element_count == 1
    assert result.availability is ObservationAvailability.CANCELLED
    assert result.prefectures == () and result.max_intensity is None


@pytest.mark.parametrize("path", [PREF + "/s:Code", PREF + "/s:Name", AREA + "/s:Code", AREA + "/s:Name"])
def test_missing_region_identity_is_rejected_without_code_or_name_guessing(path):
    with pytest.raises(EarthquakeXmlError):
        extract_jma_observed_intensity_xml(variant(DETAIL, lambda root: remove(root, path)))


@pytest.mark.parametrize("path,value", [
    (OBS + "/s:CodeDefine/s:Type", "緊急地震速報／府県予報区"),
    (AREA + "/s:Name", ""),
])
def test_wrong_code_system_or_empty_region_name_is_rejected(path, value):
    with pytest.raises(EarthquakeXmlError):
        extract_jma_observed_intensity_xml(variant(DETAIL, lambda root: set_text(root, path, value)))


@pytest.mark.parametrize("parent_path,child_path", [
    (OBS, PREF),
    (PREF, AREA),
    (AREA, AREA + "/s:MaxInt"),
    (OBS + "/s:CodeDefine", OBS + "/s:CodeDefine/s:Type"),
])
def test_duplicate_regions_values_or_code_definitions_are_ambiguous_and_rejected(parent_path, child_path):
    def duplicate(root):
        root.find(parent_path, NS).append(deepcopy(root.find(child_path, NS)))
    with pytest.raises(EarthquakeXmlError):
        extract_jma_observed_intensity_xml(variant(DETAIL, duplicate))


def test_same_identity_and_serial_revision_keeps_update_and_freshness_contracts():
    original = extract_fixture(DETAIL)
    changed = extract_jma_observed_intensity_xml(variant(
        DETAIL, lambda root: set_text(root, AREA + "/s:MaxInt", "5-"),
    ))
    assert original.report.event_id == changed.report.event_id
    assert original.report.serial == changed.report.serial
    assert original.report.report_datetime == changed.report.report_datetime
    assert classify_update(original.report, changed.report) is UpdateKind.UPDATED
    issued = changed.report.report_datetime
    assert freshness(changed.report, now=issued + timedelta(minutes=6), max_age=timedelta(minutes=5)) is Freshness.STALE
    assert original.prefectures[0].areas[0].max_intensity.code == "4"
    assert changed.prefectures[0].areas[0].max_intensity.code == "5-"
