#!/usr/bin/env python3
"""
End-to-end check of the running Aktogay twin (make aktogay-verify).

1. The twins exist in Ditto and the trucks move (simulator -> MQTT -> Ditto).
2. The Stand's web interface serves the scene and reaches Ditto through its proxy.
3. Telemetry arrives in InfluxDB (Ditto -> MQTT -> Telegraf -> InfluxDB).
4. Each what-if scenario round-trips through the platform: desiredProperties
   written to Ditto -> event on MQTT -> simulator applies it -> properties
   reported back -> consequences visible in linked twins -> undone.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV = {}
with open(os.path.join(ROOT, "deploy", "compose", ".env")) as fh:
    for line in fh:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            ENV[k] = v

DITTO = f"http://localhost:{ENV.get('DITTO_PORT', '8080')}/api/2"
UI = f"http://localhost:{ENV.get('AKTOGAY_PORT', '8070')}"
INFLUX = f"http://localhost:{ENV.get('INFLUXDB_PORT', '8086')}"
AUTH = "Basic " + base64.b64encode(f"ditto:{ENV['DITTO_PASSWORD']}".encode()).decode()
NS = "kz.aktogay"
failures = []


def http(method, url, body=None, headers=None, auth=True):
    h = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = AUTH
    h.update(headers or {})
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        return json.loads(raw) if "json" in ctype and raw else raw.decode(errors="replace")


def thing(name):
    return http("GET", f"{DITTO}/things/{NS}:{name}")


def prop(t, feature, key="value"):
    return t.get("features", {}).get(feature, {}).get("properties", {}).get(key)


def check(ok, text):
    print(f"  {'PASS' if ok else 'FAIL'}  {text}")
    if not ok:
        failures.append(text)
    return ok


def wait(fn, timeout, every=2.0):
    end = time.time() + timeout
    while True:
        try:
            value = fn()
            if value:
                return value
        except (urllib.error.URLError, KeyError, TypeError):
            pass
        if time.time() > end:
            return None
        time.sleep(every)


def set_desired(name, feature, value):
    http("PUT", f"{DITTO}/things/{NS}:{name}/features/{feature}/desiredProperties/value", value)


def forecast_after(made_at):
    f = thing("dispatcher")["features"].get("forecast", {}).get("properties", {})
    return f if f and f.get("made_at") != made_at else None


def scenario(title, name, feature, on, off, consequence):
    print(f"\nWhat-if: {title}")
    before = thing("dispatcher")["features"].get("forecast", {}).get("properties", {})
    set_desired(name, feature, on)
    check(bool(wait(lambda: prop(thing(name), feature) == on, 60)),
          f"{NS}:{name} {feature}: desired '{on}' applied by the simulator and reported back")
    fc = wait(lambda: forecast_after(before.get("made_at")), 90)
    if check(bool(fc), "dispatcher recalculated the end-of-shift forecast"):
        print(f"        forecast {fc['value']} t Cu, without scenarios {fc['baseline_cu_t']} t Cu, "
              f"loss {fc['loss_cu_t']} t Cu; ore to crusher {fc['sulphide_crusher_t']} t "
              f"(without scenarios {fc['baseline_sulphide_crusher_t']} t)")
    consequence()
    set_desired(name, feature, off)
    check(bool(wait(lambda: prop(thing(name), feature) == off, 60)), f"undone: {NS}:{name} {feature} back to '{off}'")


def main():
    print("Twins in Ditto")
    items = wait(lambda: http("GET", f"{DITTO}/search/things?namespaces={NS}&option=size(200)&fields=thingId")["items"], 120)
    check(items is not None and len(items) >= 43, f"{len(items or [])} twins in namespace {NS} (expected 43)")

    def positions():
        return {t: (prop(thing(t), "position", "x"), prop(thing(t), "position", "y")) for t in ("ht-01", "ht-05", "ht-09", "ht-13")}
    p1 = wait(positions, 60)
    time.sleep(6)
    p2 = positions()
    moved = sum(1 for k in p1 if p1[k] != p2[k])
    check(moved >= 2, f"trucks move: {moved} of 4 sampled trucks changed position in 6 s")
    mine = thing("mine")
    print(f"        shift {prop(mine, 'shift', 'number')} {prop(mine, 'shift', 'clock')}: "
          f"ore to crusher {prop(mine, 'sulphide_to_crusher')} t, Cu in ore {prop(mine, 'copper_in_ore')} t")

    print("\nStand web interface")
    html = wait(lambda: http("GET", UI + "/", auth=False), 30)
    check(bool(html) and "app.js" in html, f"{UI}/ serves the 3D scene")
    via = wait(lambda: http("GET", f"{UI}/ditto/api/2/things/{NS}:pit", auth=False), 30)
    check(bool(via) and via.get("thingId") == f"{NS}:pit", "the interface reads the twins from Ditto through its proxy")

    print("\nTelemetry history")
    token = ENV.get("INFLUXDB_TOKEN")
    flux = ('from(bucket:"default") |> range(start:-10m) |> filter(fn:(r) => r.thingId == "kz.aktogay:ht-01" '
            'and r._field == "value_speed_properties_value") |> count()')
    rows = wait(lambda: "_value" in http("POST", f"{INFLUX}/api/v2/query?org=opentwins", flux.encode(),
                                         {"Authorization": f"Token {token}", "Content-Type": "application/vnd.flux",
                                          "Accept": "application/csv"}, auth=False) and "ok", 90, 5)
    check(bool(rows), "truck telemetry reaches InfluxDB via Telegraf")

    def road_consequence():
        t = wait(lambda: thing("dispatcher") if prop(thing("dispatcher"), "reroutes") is not None else None, 10)
        print(f"        reroutes around closed roads so far: {prop(t, 'reroutes')}")

    def shovel_consequence():
        time.sleep(10)
        heading = [n for n in (f"ht-{i:02d}" for i in range(1, 15))
                   if prop(thing(n), "state") == "to_shovel" and prop(thing(n), "assignment", "shovel") == f"{NS}:ex-01"]
        check(not heading, "dispatcher sends no empty truck to the stopped EX-01")

    def crusher_consequence():
        ok = wait(lambda: any(prop(thing(n), "assignment", "destination") == f"{NS}:stockpile"
                              for n in (f"ht-{i:02d}" for i in range(1, 15))), 90)
        check(bool(ok), "sulphide ore is routed to the ROM stockpile while the crusher is down")

    def truck_consequence():
        check(prop(thing("fleet"), "trucks_down") >= 1, "fleet twin counts the truck as down")

    scenario("close the lower East ramp", "road-ramp-e-lower", "status", "closed", "open", road_consequence)
    scenario("stop excavator EX-01", "ex-01", "status", "down", "up", shovel_consequence)
    scenario("stop the primary crusher", "crusher", "status", "down", "up", crusher_consequence)
    scenario("truck HT-03 breaks down", "ht-03", "status", "down", "up", truck_consequence)
    scenario("dispatcher off: fixed truck allocation", "dispatcher", "mode", "fixed", "dynamic", lambda: None)

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
