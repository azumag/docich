"""Synthetic deterministic bulletins; these are NOT live forecasts."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from docich import weather as w

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
def serving(path, *, clock=lambda: NOW, runtime_id="preview", generation=None, lease_id=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), v.handler_for(
        path, runtime_id, generation=generation, lease_id=lease_id, clock=clock))
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
    assert "textContent=txt" in v.HTML
    assert "performance.now()" in v.HTML
    assert "Math.min(remaining,5000)" in v.HTML
    assert "AbortSignal.timeout(2000)" in v.HTML
    assert "晴れ" not in v.HTML  # no fabricated default forecast
    assert "出典：" in v.HTML and "docichが編集" in v.HTML


@pytest.mark.parametrize("value",[0,80,65536,True,"8803"])
def test_serve_has_fixed_loopback_and_validated_port(tmp_path,value):
    with pytest.raises(w.WeatherError):v.serve(tmp_path/"snapshot.json",port=value)


@pytest.mark.parametrize("value",["", "<script>", "../../secret", "x"*129])
def test_runtime_id_validation(tmp_path,value):
    with pytest.raises(w.WeatherError):v.handler_for(tmp_path/"snapshot.json",value)
