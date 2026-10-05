"""Synthetic deterministic bulletins; these are NOT live forecasts."""
from copy import deepcopy
from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from docich import weather as w, weather_map_data as map_data

NOW = datetime(2026, 9, 29, 6, tzinfo=w.JST).timestamp()
TODAY = datetime.fromtimestamp(NOW, w.JST).date()


def fixture(city, issued="2026-09-29T05:00:00+09:00"):
    # The unusual 09h,00h same-day ordering mirrors the JMA short-range product.
    return [{
        "publishingOffice": "テスト気象台", "reportDatetime": issued,
        "timeSeries": [
            {"timeDefines": ["2026-09-29T05:00:00+09:00", "2026-09-30T00:00:00+09:00"],
             "areas": [{"area": {"name": "テスト予報区域", "code": city.area},
                        "weathers": ["晴れ　夜　くもり", "雨　のち　くもり"]}]},
            {"timeDefines": [f"2026-09-{d:02}T{h:02}:00:00+09:00" for d, h in [(29,6),(29,12),(29,18),(30,0),(30,6),(30,12),(30,18)]],
             "areas": [{"area": {"code": city.area}, "pops": ["0", "10", "20", "50", "60", "70", "80"]}]},
            {"timeDefines": ["2026-09-29T09:00:00+09:00", "2026-09-29T00:00:00+09:00", "2026-09-30T00:00:00+09:00", "2026-09-30T09:00:00+09:00"],
             "areas": [{"area": {"name": city.station, "code": "test-station"}, "temps": ["21", "21", "0", "18"]}]},
        ],
    }, {"reportDatetime": "2099-01-01T00:00:00+09:00", "timeSeries": []}]


def bundle(now=NOW):
    return {"schema_version": 1, "generated_at": now, "target_date": "2026-09-29", "records": {
        c.office: {"schema_version": 1, "office": c.office, "fetched_at": now, "payload": fixture(c)} for c in w.CITIES
    }}


def test_all_eleven_cities_and_independent_area_station_axes():
    data = w.project(bundle(), now=NOW)
    assert [c["city"] for c in data["cities"]] == ["札幌", "仙台", "東京", "新潟", "名古屋", "大阪", "広島", "高松", "福岡", "鹿児島", "那覇"]
    assert all(c["high_c"] == 21 and c["low_c"] is None for c in data["cities"])
    assert data["cities"][0]["pops"] == [
        {"start": 0, "end": 6, "percent": None},
        {"start": 6, "end": 12, "percent": 0},
        {"start": 12, "end": 18, "percent": 10},
        {"start": 18, "end": 24, "percent": 20},
    ]
    assert "気象庁" in data["attribution"] and "編集" in data["attribution"]


def test_tomorrow_low_zero_is_not_missing():
    c=w.CITIES[0]
    data=w.normalize(fixture(c), c, TODAY+timedelta(days=1), NOW)
    assert data["low_c"] == 0 and data["high_c"] == 18
    assert [x["percent"] for x in data["pops"]] == [50,60,70,80]


def test_station_is_not_selected_by_position():
    c=w.CITIES[0]; raw=fixture(c)
    foreign=deepcopy(raw[0]["timeSeries"][2]["areas"][0]); foreign["area"]["name"]="別の地点"; foreign["temps"]=["55"]*4
    raw[0]["timeSeries"][2]["areas"].insert(0,foreign)
    assert w.normalize(raw,c,TODAY,NOW)["high_c"] == 21


@pytest.mark.parametrize("change", [
    lambda b: b.pop("records"),
    lambda b: b["records"].pop(w.CITIES[0].office),
    lambda b: b["records"].update({"unexpected":{}}),
    lambda b: b.update(schema_version=2),
    lambda b: b.update(generated_at=NOW+1),
    lambda b: b.update(generated_at=NOW-w.CACHE_TTL),
    lambda b: b.update(generated_at=float("nan")),
    lambda b: b.update(generated_at=True),
    lambda b: b.update(target_date="2026-09-28"),
    lambda b: b.update(target_date="2026-10-01"),
    lambda b: b["records"][w.CITIES[0].office].update(office="000000"),
    lambda b: b["records"][w.CITIES[0].office].update(fetched_at=NOW+1),
    lambda b: b["records"][w.CITIES[0].office].update(fetched_at=NOW-w.CACHE_TTL),
    lambda b: b["records"].update({w.CITIES[0].office:None}),
])
def test_bundle_fail_closed(change):
    b=bundle();change(b)
    with pytest.raises(w.WeatherError): w.project(b,now=NOW)


@pytest.mark.parametrize("value", ["101","-1","NaN","1.5",None,True,20])
def test_invalid_rain_probability(value):
    c=w.CITIES[0];raw=fixture(c);raw[0]["timeSeries"][1]["areas"][0]["pops"][0]=value
    with pytest.raises(w.WeatherError):w.normalize(raw,c,TODAY,NOW)


@pytest.mark.parametrize("value", ["<script>alert(1)</script>","雨\n晴れ","",None,"晴"*241])
def test_unsafe_weather_text(value):
    c=w.CITIES[0];raw=fixture(c);raw[0]["timeSeries"][0]["areas"][0]["weathers"][0]=value
    with pytest.raises(w.WeatherError):w.normalize(raw,c,TODAY,NOW)


@pytest.mark.parametrize("issue", ["2026-09-29T07:00:00+09:00","2026-09-28T11:00:00+09:00","2026-09-29T05:00:00","bad"])
def test_bad_issue_is_not_replaced_with_weekly_issue(issue):
    c=w.CITIES[0]
    with pytest.raises(w.WeatherError):w.normalize(fixture(c,issue),c,TODAY,NOW)


@pytest.mark.parametrize("change", [
    lambda r: r[0].update(timeSeries=[]),
    lambda r: r[0]["timeSeries"][0]["areas"].append(deepcopy(r[0]["timeSeries"][0]["areas"][0])),
    lambda r: r[0]["timeSeries"][0]["areas"][0]["area"].update(code="wrong"),
    lambda r: r[0]["timeSeries"][2]["areas"][0]["area"].update(name="wrong"),
    lambda r: r[0]["timeSeries"][1]["timeDefines"].__setitem__(1,"2026-09-29T06:00:00+09:00"),
    lambda r: r[0]["timeSeries"][1]["timeDefines"].__setitem__(0,"2026-09-29T07:00:00+09:00"),
    lambda r: r[0]["timeSeries"][2]["timeDefines"].__setitem__(0,"2026-09-29T10:00:00+09:00"),
    lambda r: r[0]["timeSeries"][2]["areas"][0]["temps"].pop(),
    lambda r: r[0]["timeSeries"][2]["areas"][0]["temps"].__setitem__(0,"100"),
])
def test_axis_and_identity_fail_closed(change):
    c=w.CITIES[0];raw=fixture(c);change(raw)
    with pytest.raises(w.WeatherError):w.normalize(raw,c,TODAY,NOW)


def test_missing_temperature_is_not_zero():
    c=w.CITIES[0];raw=fixture(c);raw[0]["timeSeries"][2]["areas"][0]["temps"][0]=""
    assert w.normalize(raw,c,TODAY,NOW)["high_c"] is None


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}',b'{"v":NaN}',b'{"v":Infinity}',b'\xff',b'not json'])
def test_json_rejects_ambiguous_values(raw):
    with pytest.raises(w.WeatherError):w.decode(raw)


def test_json_size_limit():
    with pytest.raises(w.WeatherError):w.decode(b" "*(w.MAX_BYTES+1))


def test_allowlist_has_no_arbitrary_url():
    with pytest.raises(w.WeatherError):w.url_for("https://example.invalid/")
    with pytest.raises(w.WeatherError):w.url_for("../130000")
    assert w.url_for("130000") == "https://www.jma.go.jp/bosai/forecast/data/forecast/130000.json"


def test_cache_reuses_data_but_checks_full_content(tmp_path):
    calls=[]
    def get(office,timeout):
        calls.append(office);assert 0<timeout<=4
        return fixture(next(c for c in w.CITIES if c.office==office))
    w.build_bundle(tmp_path,now=NOW,getter=get)
    assert len(calls)==11
    w.build_bundle(tmp_path,now=NOW+1,getter=get)
    assert len(calls)==11
    p=tmp_path/f"{w.CITIES[0].office}.json";v=json.loads(p.read_text());v["payload"]=[];w.write_json(p,v)
    w.build_bundle(tmp_path,now=NOW+2,getter=get)
    assert len(calls)==12


def test_stale_cache_failure_never_uses_old_forecast(tmp_path):
    w.build_bundle(tmp_path,now=NOW,getter=lambda office,t:fixture(next(c for c in w.CITIES if c.office==office)))
    def fail(*args):raise w.WeatherError("fetch-failed")
    with pytest.raises(w.WeatherError):w.build_bundle(tmp_path,now=NOW+w.CACHE_TTL,getter=fail)


def test_auto_target_switches_at_17_jst(tmp_path):
    get=lambda office,t:fixture(next(c for c in w.CITIES if c.office==office),"2026-09-29T17:00:00+09:00")
    result=w.build_bundle(tmp_path,now=NOW+11*3600,getter=get)
    assert result["target_date"] == "2026-09-30"


def test_narration_is_literal_and_has_no_invented_minimum():
    view=w.project(bundle(),now=NOW);lines=w.narration(view)
    assert len(lines)==13
    assert "9月29日" in lines[0]
    assert all("最低気温" not in line for line in lines)
    assert all("晴れ 夜 くもり" in line for line in lines[1:-1])
    assert "気象庁" in lines[-1] and "編集" in lines[-1]


def test_projected_read_view_narration_builds_all_thirteen_offline_audio_requests(tmp_path):
    from docich.weather_audio import build_weather_audio_request, weather_report_digest

    path = tmp_path / "snapshot.json"
    w.write_json(path, bundle())
    view = v.read_view(path, clock=lambda: NOW)
    lines = w.narration(view)
    runtime_identity = {
        "game": "weather-view",
        "runtime_id": "g4-a1b2c3d4",
        "generation": 4,
        "lease_id": "01234567-89ab-4cde-8123-456789abcdef",
    }

    requests = [
        build_weather_audio_request(
            execution_id="12345678-1234-4234-8234-123456789abc",
            item_index=index,
            text=line,
            runtime_identity=runtime_identity,
            forecast_view=view,
            now=NOW,
        )
        for index, line in enumerate(lines)
    ]

    assert len(requests) == 13
    assert [item["item_index"] for item in requests] == list(range(13))
    assert [item["text"] for item in requests] == lines
    assert len({item["item_key"] for item in requests}) == 13
    expected_report = weather_report_digest(view, now=NOW)
    assert {item["forecast"]["report_digest"] for item in requests} == {expected_report}
    assert [item["forecast"]["issued_at"] for item in requests[1:12]] == [
        row["issued_at"] for row in view["cities"]
    ]


# Read-only presentation and publication contracts.
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import threading
from unittest.mock import patch

import pytest

from docich import weather as w
from docich import weather_view as v


@contextmanager
def serving(path, *, clock=lambda: NOW, runtime_id="preview", generation=None, lease_id=None,
            cue_path=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), v.handler_for(
        path, runtime_id, generation=generation, lease_id=lease_id,
        cue_path=cue_path, clock=clock))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def request(port, path="/api/weather", *, method="GET", headers=None):
    con=HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        con.request(method, path, headers=headers or {})
        response=con.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        con.close()


def test_readonly_server_and_literal_narration(tmp_path):
    path=tmp_path/"snapshot.json"; w.write_json(path,bundle())
    with serving(path) as port:
        code,headers,raw=request(port)
        data=json.loads(raw)
        assert code==200 and data["ok"] and len(data["cities"])==11
        assert len(data["narration"])==13 and data["runtime_id"]=="preview"
        assert "no-store" in headers["Cache-Control"]
        assert headers["X-Content-Type-Options"]=="nosniff"
        assert "connect-src 'self'" in headers["Content-Security-Policy"]
        code,headers,raw=request(port,"/")
        assert code==200 and "気象庁" in raw.decode()
        assert "const BROADCAST=false;" in raw.decode()
        code,_,raw=request(port,"/broadcast")
        assert code==200 and "const BROADCAST=true;" in raw.decode()


def test_broadcast_cue_exposes_only_runtime_bound_audio_ordinal(tmp_path):
    path = tmp_path / "weather" / "snapshot.json"
    path.parent.mkdir()
    w.write_json(path, bundle())
    cue_path = tmp_path / "weather_corner.json"
    lease_id = "01234567-89ab-4cde-8123-456789abcdef"
    identity = {
        "game": "weather-view",
        "runtime_id": "g4-a1b2c3d4",
        "generation": 4,
        "lease_id": lease_id,
    }
    state = {
        "schema_version": 1,
        "status": "active",
        "weather_runtime_identity": identity,
        "audio_delivery": {
            "status": "running",
            "next_index": 3,
        },
    }
    cue_path.write_text(json.dumps(state), encoding="utf-8")
    with serving(
        path, runtime_id=identity["runtime_id"], generation=identity["generation"],
        lease_id=lease_id, cue_path=cue_path,
    ) as port:
        code, _, raw = request(port, "/api/weather-cue")
        data = json.loads(raw)
        assert code == 200
        assert data == {"ok": True, "status": "running", "item_index": 3}
        assert identity["runtime_id"].encode() not in raw
        assert lease_id.encode() not in raw
        assert b"requests" not in raw and b"text" not in raw

        state["audio_delivery"].update(status="completed", next_index=13)
        cue_path.write_text(json.dumps(state), encoding="utf-8")
        code, _, raw = request(port, "/api/weather-cue")
        assert code == 200
        assert json.loads(raw) == {
            "ok": True, "status": "completed", "item_index": 12,
        }

        state["weather_runtime_identity"] = {**identity, "runtime_id": "g5-other"}
        cue_path.write_text(json.dumps(state), encoding="utf-8")
        code, _, raw = request(port, "/api/weather-cue")
        assert code == 503
        assert json.loads(raw) == {"ok": False, "reason": "cue-unavailable"}


def test_readonly_server_reports_the_game_switch_generation(tmp_path):
    path=tmp_path/"snapshot.json"; w.write_json(path,bundle())
    lease_id="01234567-89ab-4cde-8123-456789abcdef"
    with serving(path,runtime_id="g4-a1b2c3d4",generation=4,lease_id=lease_id) as port:
        code,_,raw=request(port)
        data=json.loads(raw)
        assert code==200 and data["runtime_id"]=="g4-a1b2c3d4"
        assert data["generation"]==4
        assert data["lease_id"]==lease_id


@pytest.mark.parametrize("method",["POST","PUT","DELETE","PATCH"])
def test_server_has_no_mutation_api(tmp_path,method):
    with serving(tmp_path/"missing.json") as port:
        assert request(port,method=method)[0]==501
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize("path",["/snapshot.json","/../cache/130000.json","/api/trading/dashboard","/api/weather?url=https://example.invalid","/api/weather?file=/etc/passwd"])
def test_no_file_or_provider_proxy(tmp_path,path):
    with serving(tmp_path/"missing.json") as port:
        assert request(port,path)[0]==404


def test_host_header_rebinding_denied(tmp_path):
    with serving(tmp_path/"missing.json") as port:
        assert request(port,headers={"Host":"attacker.invalid"})[0]==403


def test_missing_corrupt_expired_or_deleted_snapshot_is_unavailable(tmp_path):
    path=tmp_path/"snapshot.json"; now=[NOW]
    with serving(path,clock=lambda:now[0]) as port:
        assert request(port)[0]==503
        path.write_text("not JSON")
        assert request(port)[0]==503
        w.write_json(path,bundle())
        assert request(port)[0]==200
        now[0]=NOW+w.CACHE_TTL
        assert request(port)[0]==503
        now[0]=NOW
        path.unlink()
        code,_,raw=request(port)
        assert code==503 and json.loads(raw)=={"ok":False,"reason":"forecast-unavailable"}


def test_failed_refresh_removes_previously_published_snapshot(tmp_path,capsys):
    path=tmp_path/"snapshot.json"; w.write_json(path,bundle())
    with patch.object(v,"build_bundle",side_effect=w.WeatherError("simulated")):
        assert v.main(["--state-dir",str(tmp_path),"fetch"])==2
    assert not path.exists()
    assert json.loads(capsys.readouterr().out)=={"ok":False,"reason":"forecast-unavailable"}


def test_readonly_status_does_not_fetch(tmp_path,capsys):
    with patch.object(v,"build_bundle") as fetch:
        assert v.main(["--state-dir",str(tmp_path),"status"])==2
        fetch.assert_not_called()


def test_forecast_is_not_embedded_in_script_or_assigned_as_html():
    assert "innerHTML" not in v.HTML
    assert ".textContent" in v.HTML
    assert "performance.now()" in v.HTML
    assert "Math.min(remaining,5000)" in v.HTML
    assert "AbortSignal.timeout(2000)" in v.HTML
    assert "晴れ" not in v.HTML  # no fabricated default forecast
    assert "出典：" in v.HTML and "docichが編集" in v.HTML
    assert "Natural Earth" in v.HTML and "Public Domain" in v.HTML
    assert "__MAP_DATA__" not in v.HTML
    assert "順送りズーム" in v.HTML and "全国表示" in v.HTML
    assert "音声同期" in v.HTML and "読み上げ cue：再生完了同期" in v.HTML
    assert "@media(max-aspect-ratio:5/4)" in v.HTML
    assert "fetch('/api/weather'" in v.HTML
    assert "fetch('/api/weather-cue'" in v.HTML
    assert "startBroadcastTour" not in v.HTML
    assert "startBroadcastSync" in v.HTML


def test_weather_map_is_natural_earth_and_covers_all_forecast_points():
    assert map_data.VIEWBOX == (1220, 960)
    assert len(map_data.POLYGONS) == 109
    assert set(map_data.CITY_POINTS) == {
        "sapporo", "sendai", "tokyo", "niigata", "nagoya", "osaka",
        "hiroshima", "takamatsu", "fukuoka", "kagoshima", "naha",
    }
    width, height = map_data.VIEWBOX
    assert all(0 <= x <= width and 0 <= y <= height for x, y in map_data.CITY_POINTS.values())


def test_weather_html_has_unique_ids_and_no_external_runtime_assets():
    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
            self.srcs = []
            self.resources = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.ids.append(attrs["id"])
            if "src" in attrs:
                self.srcs.append(attrs["src"])
            if tag in {"img", "iframe", "video", "audio", "link"}:
                self.resources.append(tag)

    markup = Markup()
    markup.feed(v.HTML)
    assert len(markup.ids) == len(set(markup.ids))
    assert not markup.srcs and not markup.resources
    assert "const MAP_DATA={\"polygons\":" in v.HTML
    assert v.HTML.count("fetch(") == 1


@pytest.mark.parametrize("scenario", [
    "choose", "next", "previous", "region", "marker", "tour_start", "tour_tick",
    "national", "width_overflow", "resize", "expired_choose", "expired_next",
    "expired_national", "expired_tour", "expired_tour_stop", "all_locations", "lease_expiry",
    "server_failure", "incomplete", "old_poll", "old_failure", "manual_stop", "broadcast_cycle", "broadcast_overflow", "broadcast_expiry", "broadcast_auto",
])
def test_weather_ui_control_flow_without_browser(scenario):
    # No renderer: mocked layout forces overflow so each interaction must fail closed.
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable for DOM-free UI control-flow tests")
    script = v.HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    if scenario == "broadcast_auto":
        script = script.replace("const BROADCAST=false;", "const BROADCAST=true;")
    runner = Path(__file__).parent / "fixtures/weather_view/contract.js"
    result = subprocess.run(
        [node, str(runner)], input=json.dumps({"script": script, "scenario": scenario}),
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("value",[0,80,65536,True,"8803"])
def test_serve_has_fixed_loopback_and_validated_port(tmp_path,value):
    with pytest.raises(w.WeatherError):v.serve(tmp_path/"snapshot.json",port=value)


@pytest.mark.parametrize("value",["", "<script>", "../../secret", "x"*129])
def test_runtime_id_validation(tmp_path,value):
    with pytest.raises(w.WeatherError):v.handler_for(tmp_path/"snapshot.json",value)


def test_refresh_snapshot_rejects_bundle_before_publication(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    w.write_json(path, bundle())
    monkeypatch.setattr(v, "build_bundle", lambda *_args, **_kw: bundle())
    monkeypatch.setattr(v.time, "time", lambda: NOW + w.CACHE_TTL)
    with pytest.raises(w.WeatherError):
        v.refresh_snapshot(tmp_path)
    assert not path.exists()


def test_refresh_snapshot_shares_nonblocking_lock(tmp_path):
    import fcntl
    path = tmp_path / "snapshot.json"
    w.write_json(path, bundle())
    with (tmp_path / ".fetch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            v.refresh_snapshot(tmp_path)
    assert path.exists()  # Concurrent caller cannot invalidate the owner's data.
