#!/usr/bin/env python3
"""
Aktogay haulage simulator: the field gateway of the twin.

Publishes the state of every twin to MQTT as Ditto protocol "merge" commands
(topic telemetry/<thing>), and listens to the twin events Ditto sends back on
opentwins/#. When a desiredProperties value changes (a what-if scenario run
from the Stand), it applies it to the simulation and reports the new value
in properties. The simulator never writes twin state over HTTP.
"""

import argparse
import base64
import copy
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import urllib.request

import model as M
from mine import Mine
from twins import snapshot

log = logging.getLogger("aktogay")
TOPIC_PREFIX = "telemetry"
STEP_MAX_S = 5.0


def merge_command(name, features):
    return {
        "topic": f"{M.NAMESPACE}/{name}/things/twin/commands/merge",
        "headers": {"content-type": "application/merge-patch+json"},
        "path": "/features",
        "value": features,
    }


def apply_desired(mine, name, feature, value):
    """Apply one desired value to the simulation; returns True if it was a known control."""
    if not isinstance(value, str):
        return False
    if name.startswith("road-") and feature == "status" and value in ("open", "closed"):
        mine.set_road(name[len("road-"):], value == "closed")
    elif name in M.SHOVELS and feature == "status" and value in ("up", "down"):
        mine.set_shovel(name, value)
    elif name in M.TRUCKS and feature == "status" and value in ("up", "down"):
        mine.set_truck(name, value)
    elif name == "crusher" and feature == "status" and value in ("up", "down"):
        mine.set_crusher(value)
    elif name == "dispatcher" and feature == "mode" and value in ("dynamic", "fixed"):
        mine.set_mode(value)
    else:
        return False
    return True


def desired_from_event(event):
    """Yield (thing name, feature, value) for desiredProperties changes in a Ditto event."""
    topic = event.get("topic", "").split("/")
    if len(topic) < 6 or topic[0] != M.NAMESPACE or topic[4] != "events":
        return
    name, path, value = topic[1], event.get("path", ""), event.get("value")
    parts = [p for p in path.split("/") if p]
    # /features/<f>/desiredProperties/value, /features/<f>/desiredProperties, /features/<f>, /features
    if parts[:1] != ["features"]:
        return
    if len(parts) >= 3 and parts[2] == "desiredProperties":
        if len(parts) == 4 and parts[3] == "value":
            yield name, parts[1], value
        elif len(parts) == 3 and isinstance(value, dict):
            yield name, parts[1], value.get("value")
    elif len(parts) == 2 and isinstance(value, dict):
        yield name, parts[1], (value.get("desiredProperties") or {}).get("value")
    elif len(parts) == 1 and isinstance(value, dict):
        for feature, body in value.items():
            if isinstance(body, dict) and isinstance(body.get("desiredProperties"), dict):
                yield name, feature, body["desiredProperties"].get("value")


def initial_desired(ditto_url, password):
    """Desired values already stored in Ditto, so a restart keeps active scenarios."""
    auth = "Basic " + base64.b64encode(f"ditto:{password}".encode()).decode()
    url = (f"{ditto_url}/api/2/search/things?namespaces={M.NAMESPACE}"
           "&fields=thingId,features/status/desiredProperties,features/mode/desiredProperties&option=size(200)")
    req = urllib.request.Request(url, headers={"Authorization": auth})
    with urllib.request.urlopen(req, timeout=20) as resp:
        items = json.load(resp).get("items", [])
    for item in items:
        name = item["thingId"].split(":", 1)[1]
        for feature, body in item.get("features", {}).items():
            yield name, feature, (body.get("desiredProperties") or {}).get("value")


class Forecaster(threading.Thread):
    """Runs the end-of-shift forecast on a copy of the mine in the background."""

    def __init__(self):
        super().__init__(daemon=True)
        self.jobs = queue.Queue(maxsize=1)
        self.result = None

    def submit(self, mine_copy):
        try:
            self.jobs.put_nowait(mine_copy)
        except queue.Full:
            pass

    def run(self):
        while True:
            mine = self.jobs.get()
            started = time.time()
            self.result = mine.forecast()
            log.info("forecast at %s: %.1f t Cu (baseline %.1f) in %.1fs", mine.clock(),
                     self.result["current"]["cu_t"], self.result["baseline"]["cu_t"], time.time() - started)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "localhost"))
    p.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--ditto-url", default=os.environ.get("DITTO_URL", "http://localhost:8080"))
    p.add_argument("--interval", type=float, default=float(os.environ.get("SIM_INTERVAL", "1")),
                   help="real seconds between publishes (default 1)")
    p.add_argument("--speedup", type=float, default=float(os.environ.get("SIM_SPEEDUP", "8")),
                   help="simulated seconds per real second (default 8)")
    p.add_argument("--forecast-every", type=float, default=float(os.environ.get("SIM_FORECAST_EVERY", "30")),
                   help="real seconds between shift forecasts (default 30)")
    p.add_argument("--seed", type=int, default=int(os.environ.get("SIM_SEED", "42")))
    p.add_argument("--dry-run", action="store_true", help="print messages instead of publishing")
    p.add_argument("--steps", type=int, default=0, help="stop after N publishes (0 = run forever)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mine = Mine(args.seed)
    inbox = queue.Queue()
    forecaster = Forecaster()
    forecaster.start()

    client = None
    if not args.dry_run:
        import paho.mqtt.client as mqtt

        password = os.environ.get("DITTO_PASSWORD")
        if password:
            try:
                for item in initial_desired(args.ditto_url, password):
                    inbox.put(item)
            except OSError as exc:
                log.warning("could not read desired state from Ditto: %s", exc)

        def on_connect(client, userdata, flags, reason, properties=None):
            client.subscribe("opentwins/#", qos=1)

        def on_message(client, userdata, msg):
            try:
                event = json.loads(msg.payload)
            except ValueError:
                return
            for item in desired_from_event(event):
                inbox.put(item)

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aktogay-sim-{os.getpid()}")
        client.on_connect = on_connect
        client.on_message = on_message
        for _ in range(60):
            try:
                client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
                break
            except OSError as exc:
                log.warning("MQTT %s:%s not reachable (%s), retrying", args.mqtt_host, args.mqtt_port, exc)
                time.sleep(2)
        else:
            log.error("giving up on MQTT %s:%s", args.mqtt_host, args.mqtt_port)
            return 1
        client.loop_start()
        log.info("publishing to mqtt://%s:%s every %.1fs at %.0fx speed",
                 args.mqtt_host, args.mqtt_port, args.interval, args.speedup)

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    published = 0
    last_sent = {}
    last_forecast = -1e9
    while not stop:
        changed = False
        while not inbox.empty():
            name, feature, value = inbox.get()
            if apply_desired(mine, name, feature, value):
                log.info("what-if: %s/%s -> %s", name, feature, value)
                changed = True
        if changed or time.time() - last_forecast >= args.forecast_every:
            forecaster.submit(copy.deepcopy(mine))
            last_forecast = time.time()

        sim_dt = args.interval * args.speedup
        while sim_dt > 1e-9:
            dt = min(STEP_MAX_S, sim_dt)
            mine.step(dt)
            sim_dt -= dt

        for name, features in snapshot(mine, forecaster.result).items():
            body = json.dumps(features, sort_keys=True, ensure_ascii=False)
            if last_sent.get(name) == body and published % 30:
                continue          # unchanged; resent every 30 ticks anyway
            last_sent[name] = body
            message = json.dumps(merge_command(name, features), ensure_ascii=False)
            if client:
                client.publish(f"{TOPIC_PREFIX}/{name}", message, qos=0)
            elif args.steps and published == args.steps - 1:
                print(message)
        published += 1
        if published % 60 == 0:
            tot = mine.totals()
            log.info("shift %d %s | crusher %d t, stockpile %d t, leach %d t, dump %d t, Cu %.1f t",
                     mine.shift, mine.clock(), tot["sulphide_crusher_t"], tot["sulphide_stockpile_t"],
                     tot["oxide_t"], tot["waste_t"], tot["cu_t"])
        if args.steps and published >= args.steps:
            break
        time.sleep(args.interval)

    if client:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
