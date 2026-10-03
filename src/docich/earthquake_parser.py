"""Pure, non-broadcast parsing of JMA earthquake XML report envelopes.

This module does not fetch feeds, schedule work, create alerts, or access an
audio/notification queue. Callers must choose an explicit freshness limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Final
import xml.etree.ElementTree as ET


JMA_NAMESPACE: Final = "http://xml.kishou.go.jp/jmaxml1/"
HEAD_NAMESPACE: Final = "http://xml.kishou.go.jp/jmaxml1/informationBasis1/"
BODY_NAMESPACE_PREFIX: Final = "http://xml.kishou.go.jp/jmaxml1/body/"
_EARTHQUAKE_TERMS: Final = ("地震", "震度", "震源", "南海トラフ")


class EarthquakeXmlError(ValueError):
    """An input is not a supported, safely parsed JMA earthquake report."""


class ControlStatus(str, Enum):
    NORMAL = "normal"
    TRAINING = "training"
    TEST = "test"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    FUTURE = "future"
    UNKNOWN = "unknown"


class UpdateKind(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    UPDATED = "updated"
    STALE = "stale"


@dataclass(frozen=True)
class JmaEarthquakeReport:
    control_title: str
    control_status: str
    control_status_kind: ControlStatus
    editorial_office: str | None
    publishing_office: str | None
    title: str
    report_datetime: datetime | None
    target_datetime: datetime | None
    valid_datetime: datetime | None
    event_id: str | None
    info_type: str
    info_kind: str | None
    info_kind_version: str | None
    serial: str | None
    serial_element_present: bool
    headline_text: str
    body_texts: tuple[str, ...]
    intensity_element_count: int
    body_sha256: str
    document_sha256: str

    @property
    def is_training(self) -> bool:
        return self.control_status_kind is ControlStatus.TRAINING

    @property
    def is_test(self) -> bool:
        return self.control_status_kind is ControlStatus.TEST

    @property
    def is_cancellation(self) -> bool:
        return self.info_type == "取消" or self.control_status == "取消"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _children(parent: ET.Element, name: str, *, same_namespace: bool = True) -> list[ET.Element]:
    parent_namespace = _namespace(parent.tag)
    return [
        child
        for child in list(parent)
        if _local_name(child.tag) == name
        and (not same_namespace or _namespace(child.tag) == parent_namespace)
    ]


def _child(
    parent: ET.Element | None,
    name: str,
    *,
    required: bool = False,
    same_namespace: bool = True,
) -> ET.Element | None:
    matches = _children(parent, name, same_namespace=same_namespace) if parent is not None else []
    if len(matches) > 1:
        raise EarthquakeXmlError("duplicate-field")
    if not matches:
        if required:
            raise EarthquakeXmlError("missing-field")
        return None
    return matches[0]


def _element_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _field(parent: ET.Element | None, name: str, *, required: bool = False) -> str | None:
    value = _element_text(_child(parent, name, required=required))
    if required and value is None:
        raise EarthquakeXmlError("empty-field")
    return value


class _RejectDtdTreeBuilder(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise EarthquakeXmlError("unsafe-xml-declaration")


def _datetime_field(parent: ET.Element, name: str) -> datetime | None:
    value = _field(parent, name)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EarthquakeXmlError("invalid-timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EarthquakeXmlError("missing-timezone")
    return parsed


def _status_kind(status: str, control_title: str, title: str) -> ControlStatus:
    if status == "訓練":
        return ControlStatus.TRAINING
    if status == "試験" or "テスト" in control_title or "テスト" in title:
        return ControlStatus.TEST
    if status == "通常":
        return ControlStatus.NORMAL
    return ControlStatus.UNKNOWN


def _validate_section_namespaces(
    control: ET.Element, head: ET.Element, body: ET.Element | None
) -> None:
    if _namespace(control.tag) != JMA_NAMESPACE:
        raise EarthquakeXmlError("invalid-control-namespace")
    if _namespace(head.tag) not in {JMA_NAMESPACE, HEAD_NAMESPACE}:
        raise EarthquakeXmlError("invalid-head-namespace")
    if body is not None:
        body_namespace = _namespace(body.tag)
        if body_namespace != JMA_NAMESPACE and not body_namespace.startswith(BODY_NAMESPACE_PREFIX):
            raise EarthquakeXmlError("invalid-body-namespace")


def parse_jma_earthquake_xml(raw: bytes | bytearray | memoryview | str) -> JmaEarthquakeReport:
    """Parse one JMA earthquake XML document without producing side effects.

    Empty or absent ``Serial`` values stay ``None``; they are never inferred.
    The exact input-byte digest is intentionally conservative: any source
    change stays visible to the update classifier, including a body revision
    that reuses the same EventID and Serial.
    """
    return _parse_report_and_body(raw)[0]


def _parse_report_and_body(
    raw: bytes | bytearray | memoryview | str,
) -> tuple[JmaEarthquakeReport, ET.Element | None]:
    if isinstance(raw, str):
        source = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        source = bytes(raw)
    else:
        raise EarthquakeXmlError("invalid-input")
    try:
        parser = ET.XMLParser(target=_RejectDtdTreeBuilder())
        root = ET.fromstring(source, parser=parser)
    except EarthquakeXmlError:
        raise
    except (ET.ParseError, ValueError) as exc:
        raise EarthquakeXmlError("invalid-xml") from exc
    if _local_name(root.tag) != "Report" or _namespace(root.tag) != JMA_NAMESPACE:
        raise EarthquakeXmlError("unsupported-document")

    control = _child(root, "Control", required=True)
    head = _child(root, "Head", required=True, same_namespace=False)
    body = _child(root, "Body", same_namespace=False)
    assert control is not None and head is not None
    _validate_section_namespaces(control, head, body)

    control_title = _field(control, "Title", required=True)
    control_status = _field(control, "Status", required=True)
    title = _field(head, "Title", required=True)
    info_type = _field(head, "InfoType", required=True)
    assert control_title is not None and control_status is not None
    assert title is not None and info_type is not None

    info_kind = _field(head, "InfoKind")
    earthquake_context = " ".join((control_title, title, info_kind or ""))
    if not any(term in earthquake_context for term in _EARTHQUAKE_TERMS):
        raise EarthquakeXmlError("not-earthquake-report")

    serial_element = _child(head, "Serial")
    serial = _element_text(serial_element)
    headline = _child(head, "Headline")
    headline_text = _field(headline, "Text") or ""
    body_namespace = _namespace(body.tag) if body is not None else None
    body_texts = tuple(
        text
        for element in (body.iter() if body is not None else ())
        if _local_name(element.tag) == "Text" and _namespace(element.tag) == body_namespace
        if (text := _element_text(element)) is not None
    )
    body_bytes = (
        ET.tostring(body, encoding="utf-8", short_empty_elements=True)
        if body is not None
        else b""
    )

    report = JmaEarthquakeReport(
        control_title=control_title,
        control_status=control_status,
        control_status_kind=_status_kind(control_status, control_title, title),
        editorial_office=_field(control, "EditorialOffice"),
        publishing_office=_field(control, "PublishingOffice"),
        title=title,
        report_datetime=_datetime_field(head, "ReportDateTime"),
        target_datetime=_datetime_field(head, "TargetDateTime"),
        valid_datetime=_datetime_field(head, "ValidDateTime"),
        event_id=_field(head, "EventID"),
        info_type=info_type,
        info_kind=info_kind,
        info_kind_version=_field(head, "InfoKindVersion"),
        serial=serial,
        serial_element_present=serial_element is not None,
        headline_text=headline_text,
        body_texts=body_texts,
        intensity_element_count=sum(
            1
            for element in (body.iter() if body is not None else ())
            if _local_name(element.tag) == "Intensity"
            and _namespace(element.tag) == body_namespace
        ),
        body_sha256=sha256(body_bytes).hexdigest(),
        document_sha256=sha256(source).hexdigest(),
    )
    return report, body


def freshness(
    report: JmaEarthquakeReport,
    *,
    now: datetime,
    max_age: timedelta,
) -> Freshness:
    """Evaluate freshness against an explicit caller-supplied policy.

    ``ReportDateTime`` is the only freshness clock used. Missing timestamps
    remain unknown; no machine clock, event origin time, target time, or file
    modification time is substituted.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise EarthquakeXmlError("missing-clock-timezone")
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        raise EarthquakeXmlError("invalid-freshness-window")
    issued = report.report_datetime
    if issued is None:
        return Freshness.UNKNOWN
    age = now.astimezone(timezone.utc) - issued.astimezone(timezone.utc)
    if age < timedelta(0):
        return Freshness.FUTURE
    return Freshness.FRESH if age <= max_age else Freshness.STALE


def _report_identity(
    report: JmaEarthquakeReport,
) -> tuple[str, str, str, str | None, str] | None:
    if report.event_id is None or report.publishing_office is None:
        return None
    return (
        report.publishing_office,
        report.control_title,
        report.title,
        report.info_kind,
        report.event_id,
    )


def classify_update(previous: JmaEarthquakeReport, incoming: JmaEarthquakeReport) -> UpdateKind:
    """Compare two reports without treating EventID or Serial as a dedupe key."""
    if previous.document_sha256 == incoming.document_sha256:
        return UpdateKind.DUPLICATE
    previous_identity = _report_identity(previous)
    incoming_identity = _report_identity(incoming)
    if previous_identity is None or incoming_identity is None or previous_identity != incoming_identity:
        return UpdateKind.NEW
    if (
        previous.report_datetime is not None
        and incoming.report_datetime is not None
        and incoming.report_datetime < previous.report_datetime
    ):
        return UpdateKind.STALE
    return UpdateKind.UPDATED


class ObservedProduct(str, Enum):
    INTENSITY_BULLETIN = "VXSE51"
    EARTHQUAKE_INTENSITY = "VXSE53"


class ObservationAvailability(str, Enum):
    PRESENT = "present"
    MISSING = "missing"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


class IntensityState(str, Enum):
    KNOWN = "known"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ObservedIntensity:
    """A literal MaxInt value; no numeric rank, inferred value, or threshold."""

    raw: str | None
    state: IntensityState

    @property
    def code(self) -> str | None:
        return self.raw if self.state is IntensityState.KNOWN else None


@dataclass(frozen=True)
class ObservedArea:
    code_type: str
    code: str
    name: str
    max_intensity: ObservedIntensity
    revise: str | None


@dataclass(frozen=True)
class ObservedPrefecture:
    code_type: str
    code: str
    name: str
    max_intensity: ObservedIntensity
    revise: str | None
    areas: tuple[ObservedArea, ...]


@dataclass(frozen=True)
class JmaObservedIntensity:
    """Extracted observations with their full report provenance attached.

    PRESENT describes XML structure, not eligibility for a normal alert.
    Training/test/unknown status remains in ``report.control_status_kind``.
    """

    report: JmaEarthquakeReport
    product: ObservedProduct | None
    availability: ObservationAvailability
    max_intensity: ObservedIntensity | None = None
    prefectures: tuple[ObservedPrefecture, ...] = ()


_OBSERVED_PRODUCTS: Final = {
    ("震度速報", "震度速報", "震度速報"): ObservedProduct.INTENSITY_BULLETIN,
    ("震源・震度に関する情報", "震源・震度情報", "地震情報"): ObservedProduct.EARTHQUAKE_INTENSITY,
}
_OBSERVED_VERSIONS: Final = frozenset({"1.0_0", "1.0_1"})
_OBSERVED_CODES: Final = frozenset({"1", "2", "3", "4", "5-", "5+", "6-", "6+", "7"})
_BULLETIN_CODES: Final = _OBSERVED_CODES - {"1", "2"}
_OBSERVED_CODE_TYPES: Final = {
    "Pref/Code": "地震情報／都道府県等",
    "Pref/Area/Code": "地震情報／細分区域",
}
_SEISMOLOGY_NAMESPACE: Final = BODY_NAMESPACE_PREFIX + "seismology1/"


def _observed_intensity(parent: ET.Element, product: ObservedProduct) -> ObservedIntensity:
    value = _field(parent, "MaxInt")
    if value is None:
        return ObservedIntensity(None, IntensityState.MISSING)
    codes = _BULLETIN_CODES if product is ObservedProduct.INTENSITY_BULLETIN else _OBSERVED_CODES
    state = IntensityState.KNOWN if value in codes else IntensityState.UNKNOWN
    return ObservedIntensity(value, state)


def _validate_observation_code_types(observation: ET.Element) -> None:
    definitions = _child(observation, "CodeDefine", required=True)
    assert definitions is not None
    for path, code_type in _OBSERVED_CODE_TYPES.items():
        matches = [element for element in _children(definitions, "Type")
                   if element.get("xpath") == path]
        if len(matches) != 1 or _element_text(matches[0]) != code_type:
            raise EarthquakeXmlError("unsupported-observation-code-definition")


def _region_identity(element: ET.Element) -> tuple[str, str]:
    code = _field(element, "Code", required=True)
    name = _field(element, "Name", required=True)
    assert code is not None and name is not None
    return code, name


def extract_jma_observed_intensity_xml(
    raw: bytes | bytearray | memoryview | str,
) -> JmaObservedIntensity:
    """Extract Pref/Area observations for VXSE51 and near-earthquake VXSE53.

    Product identity comes from Control/Head fields, never a filename.
    Forecast, headline, city, and station values cannot fill missing MaxInt.
    Cancellation returns no observation rows, even if an input retains them.
    """
    report, body = _parse_report_and_body(raw)
    product = _OBSERVED_PRODUCTS.get((report.control_title, report.title, report.info_kind))
    if report.is_cancellation:
        return JmaObservedIntensity(report, product, ObservationAvailability.CANCELLED)
    if (
        product is None
        or report.info_kind_version not in _OBSERVED_VERSIONS
        or report.info_type not in {"発表", "訂正"}
    ):
        return JmaObservedIntensity(report, product, ObservationAvailability.UNSUPPORTED)
    if body is not None and _namespace(body.tag) != _SEISMOLOGY_NAMESPACE:
        raise EarthquakeXmlError("unsupported-observation-namespace")
    intensity = _child(body, "Intensity")
    observation = _child(intensity, "Observation")
    if observation is None:
        return JmaObservedIntensity(report, product, ObservationAvailability.MISSING)

    _validate_observation_code_types(observation)
    prefs = _children(observation, "Pref")
    if not prefs:
        raise EarthquakeXmlError("missing-observation-prefecture")
    prefectures = []
    pref_codes: set[str] = set()
    area_codes: set[str] = set()
    for pref in prefs:
        pref_code, pref_name = _region_identity(pref)
        if pref_code in pref_codes:
            raise EarthquakeXmlError("duplicate-observation-prefecture")
        pref_codes.add(pref_code)
        area_elements = _children(pref, "Area")
        if not area_elements:
            raise EarthquakeXmlError("missing-observation-area")
        areas = []
        for area in area_elements:
            area_code, area_name = _region_identity(area)
            if area_code in area_codes:
                raise EarthquakeXmlError("duplicate-observation-area")
            area_codes.add(area_code)
            areas.append(ObservedArea(
                code_type=_OBSERVED_CODE_TYPES["Pref/Area/Code"],
                code=area_code,
                name=area_name,
                max_intensity=_observed_intensity(area, product),
                revise=_field(area, "Revise"),
            ))
        prefectures.append(ObservedPrefecture(
            code_type=_OBSERVED_CODE_TYPES["Pref/Code"],
            code=pref_code,
            name=pref_name,
            max_intensity=_observed_intensity(pref, product),
            revise=_field(pref, "Revise"),
            areas=tuple(areas),
        ))
    return JmaObservedIntensity(
        report=report,
        product=product,
        availability=ObservationAvailability.PRESENT,
        max_intensity=_observed_intensity(observation, product),
        prefectures=tuple(prefectures),
    )
