"""
Twin definitions (thing bodies for Ditto) and the mapping from the simulation
state to twin features.

Static data the 3D scene needs (road polylines, pit shell, positions of the
sites) is stored in twin attributes, so the scene is built from Ditto alone.
Operating conditions a what-if changes are features with desiredProperties:
the Stand writes the desired value, the simulator applies it and reports it
back in properties.
"""

import model as M
from mine import load_time_s, passes_needed

NS = M.NAMESPACE
POLICY = "default:basic_policy"


def tid(name):
    return f"{NS}:{name}"


def _status(value):
    return {"properties": {"value": value}, "desiredProperties": {"value": value}}


def _thing(name, parent, kind, label, attributes=None, features=None):
    attrs = {"name": label, "kind": kind, "_isType": False}
    if parent:
        attrs["_parents"] = tid(parent)
    attrs.update(attributes or {})
    return tid(name), {"policyId": POLICY, "attributes": attrs, "features": features or {}}


def things():
    """All twins in creation order (parents first)."""
    out = []
    out.append(_thing("mine", None, "enterprise", "Актогайский ГОК (KAZ Minerals) — транспортный сектор карьера", {
        "location": {"lat": 46.95, "lon": 79.97},
        "description": "Сектор карьера Актогай: 4 забоя, 4 погрузочные машины, 14 самосвалов CAT 793F, "
                       "дробилка ЗКД сульфидной фабрики, кучное выщелачивание окисленной руды, отвал.",
        "shift_hours": M.SHIFT_HOURS,
    }))
    out.append(_thing("pit", "mine", "open-pit", "Карьер Актогай (схема сектора)", {
        "rim_a_m": M.RIM_A_M, "rim_b_m": M.RIM_B_M, "bench_height_m": M.BENCH_HEIGHT_M,
        "benches": M.BENCHES, "bench_step_m": M.BENCH_STEP_M, "ramp_grade": M.RAMP_GRADE,
    }))
    out.append(_thing("fleet", "mine", "fleet", "Парк самосвалов", {"model": M.TRUCK_SPEC["model"], "size": len(M.TRUCKS)}, {
        "trucks_up": {"properties": {"value": len(M.TRUCKS), "unit": "шт"}},
    }))
    out.append(_thing("dispatcher", "mine", "dispatcher", "Диспетчер АСУ ГТК (модуль «Транспорт и диспетчеризация»)", {
        "plan_tph": M.PLAN_TPH,
    }, {"mode": _status("dynamic")}))

    for road, label in M.ROADS.items():
        segs = [{"id": e["id"], "pts": e["pts"], "length_m": e["length"],
                 "grade_pct": round(100 * e["rise"] / e["length"], 1)}
                for e in M.EDGES.values() if e["road"] == road]
        length = round(sum(s["length_m"] for s in segs))
        out.append(_thing(f"road-{road}", "pit", "haul-road", label, {
            "segments": segs, "length_m": length,
            "connects": sorted({n for e in M.EDGES.values() if e["road"] == road for n in (e["a"], e["b"])
                                if not n.startswith(("re", "rw"))}),
        }, {"status": _status("open")}))

    for fid, face in M.FACES.items():
        out.append(_thing(fid, "pit", "face", face["name"], {
            "bench": face["bench"], "elevation_m": M.bench_z(face["bench"]),
            "material": face["material"], "mean_grade_cu_pct": face["grade"], "position": M.NODES[fid],
        }))

    for sid, s in M.SHOVELS.items():
        out.append(_thing(sid, "pit", "loading-unit", s["name"], {
            "model": s["model"], "bucket_m3": s["bucket_m3"], "cycle_s": s["cycle_s"],
            "passes_per_truck": passes_needed(sid), "load_time_s": load_time_s(sid),
            "face": tid(s["face"]), "position": M.NODES[s["face"]], "plan_tph": M.PLAN_TPH[sid],
        }, {"status": _status("up")}))

    for t in M.TRUCKS:
        out.append(_thing(t, "fleet", "haul-truck", f"Самосвал {t.upper()}", {
            "model": M.TRUCK_SPEC["model"], "payload_t": M.TRUCK_SPEC["payload_t"],
            "engine_kw": M.TRUCK_SPEC["engine_kw"], "tank_l": M.TRUCK_SPEC["tank_l"],
        }, {"status": _status("up")}))

    out.append(_thing("crusher", "mine", "primary-crusher", "Дробилка крупного дробления (ЗКД) сульфидной фабрики", {
        "rated_tph": M.CRUSHER["rated_tph"], "bin_t": M.CRUSHER["bin_t"], "bays": M.CRUSHER["bays"],
        "position": M.NODES["crusher"], "feeds": "Сульфидная обогатительная фабрика (25 млн т/год)",
    }, {"status": _status("up")}))
    for name, kind, label in [
        ("stockpile", "rom-stockpile", "Рудный склад сульфидной руды (перегрузка)"),
        ("leach", "heap-leach", "Штабель кучного выщелачивания окисленной руды"),
        ("dump", "waste-dump", "Отвал вскрышных пород"),
        ("fuel", "fuel-bay", "Топливозаправочный пункт"),
    ]:
        out.append(_thing(name, "mine", kind, label, {"position": M.NODES[name]}))
    return out


# ----------------------------------------------------------------- features
def _v(value, unit=None):
    p = {"value": value}
    if unit:
        p["unit"] = unit
    return {"properties": p}


def snapshot(mine, forecast=None):
    """Features of every twin: {thing name: {feature: {"properties": {...}}}}."""
    hours = max(mine.t, 60.0) / 3600
    out = {}
    tot = mine.totals()
    up = [t for t in mine.trucks.values() if t.status == "up"]
    fuel = sum(t.fuel_used_l for t in mine.trucks.values())
    moved = sum(t.tonnes for t in mine.trucks.values())
    out["mine"] = {
        "shift": {"properties": {"number": mine.shift, "clock": mine.clock(), "elapsed_h": round(mine.t / 3600, 2),
                                 "value": mine.clock()}},
        "sulphide_to_crusher": _v(tot["sulphide_crusher_t"], "t"),
        "sulphide_to_stockpile": _v(tot["sulphide_stockpile_t"], "t"),
        "oxide_to_leach": _v(tot["oxide_t"], "t"),
        "waste_to_dump": _v(tot["waste_t"], "t"),
        "copper_in_ore": _v(tot["cu_t"], "t Cu"),
        "ore_rate": _v(round((tot["sulphide_crusher_t"] + tot["sulphide_stockpile_t"] + tot["oxide_t"]) / hours), "t/h"),
        "fuel_per_tonne": _v(round(fuel / moved, 3) if moved else 0, "l/t"),
    }
    cycles = [t.last_cycle_min for t in mine.trucks.values() if t.last_cycle_min]
    out["fleet"] = {
        "trucks_up": _v(len(up), "шт"),
        "trucks_down": _v(len(mine.trucks) - len(up), "шт"),
        "avg_cycle": _v(round(sum(cycles) / len(cycles), 1) if cycles else 0, "min"),
        "queue_time": _v(round(sum(t.queue_s for t in mine.trucks.values()) / 60 / len(mine.trucks), 1), "min/truck"),
        "fuel_used": _v(round(fuel), "l"),
        "productivity": _v(round(moved / hours / max(1, len(up))), "t/h per truck"),
    }

    on_road = {}
    for t in mine.trucks.values():
        if t.path:
            on_road.setdefault(M.EDGES[t.path[0][0]]["road"], []).append(t)
    for road in M.ROADS:
        trucks = on_road.get(road, [])
        out[f"road-{road}"] = {
            "status": _v("closed" if road in mine.roads.closed else "open"),
            "traffic": {"properties": {"value": len(trucks), "unit": "trucks",
                                       "trucks": ",".join(t.id for t in trucks),
                                       "avg_speed_kmh": round(sum(t.speed_kmh for t in trucks) / len(trucks), 1) if trucks else 0}},
        }

    for fid in M.FACES:
        sid = next(s for s, v in M.SHOVELS.items() if v["face"] == fid)
        out[fid] = {
            "grade": _v(round(mine.face_grade[fid], 3) if M.FACES[fid]["grade"] else 0, "% Cu"),
            "mined": _v(round(mine.shovels[sid].tonnes), "t"),
            "served_by": _v(tid(sid)),
        }

    en_route = {}
    for t in mine.trucks.values():
        if t.state == "to_shovel" and t.status == "up":
            en_route[t.shovel] = en_route.get(t.shovel, 0) + 1
    for sid, sh in mine.shovels.items():
        busy = sh.busy_s + sh.idle_s
        state = "down" if sh.status != "up" else ("loading" if sh.loading else "waiting_for_trucks")
        out[sid] = {
            "status": _v(sh.status),
            "state": _v(state),
            "loading_truck": _v(sh.loading or ""),
            "queue": {"properties": {"value": len(sh.queue), "unit": "trucks", "trucks": ",".join(sh.queue),
                                     "en_route": en_route.get(sid, 0)}},
            "production": {"properties": {"value": round(sh.tonnes), "unit": "t",
                                          "rate_tph": round(sh.tonnes / hours), "plan_tph": M.PLAN_TPH[sid],
                                          "plan_pct": round(100 * sh.tonnes / (M.PLAN_TPH[sid] * hours))}},
            "utilization": _v(round(100 * sh.busy_s / busy, 1) if busy else 0, "%"),
            "passes": _v(sh.passes, "passes"),
        }

    for t in mine.trucks.values():
        (x, y, z), heading = t.xyz()
        road = M.EDGES[t.path[0][0]]["road"] if t.path else ""
        out[t.id] = {
            "status": _v(t.status),
            "state": _v(t.state if t.status == "up" else "down"),
            "position": {"properties": {"x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
                                        "heading": round(heading), "road": road, "node": t.node}},
            "assignment": {"properties": {"shovel": tid(t.shovel) if t.shovel else "",
                                          "destination": tid(t.dest) if t.dest and t.dest in
                                          ("crusher", "stockpile", "leach", "dump") else "",
                                          "home_shovel": tid(t.home) if t.home else ""}},
            "payload": {"properties": {"value": round(t.payload_t, 1), "unit": "t", "material": t.material,
                                       "grade_cu_pct": round(t.grade, 3)}},
            "speed": _v(round(t.speed_kmh, 1), "km/h"),
            "engine_power": _v(round(t.power_kw), "kW"),
            "fuel": {"properties": {"value": round(100 * t.fuel_l / M.TRUCK_SPEC["tank_l"], 1), "unit": "%",
                                    "litres": round(t.fuel_l)}},
            "shift_stats": {"properties": {"loads": t.loads, "tonnes": round(t.tonnes),
                                           "last_cycle_min": round(t.last_cycle_min, 1),
                                           "queue_min": round(t.queue_s / 60, 1), "fuel_used_l": round(t.fuel_used_l)}},
        }

    c = mine.crusher
    out["crusher"] = {
        "status": _v(c.status),
        "state": _v("down" if c.status != "up" else ("crushing" if c.bin_t > 1 else "starved")),
        "bin": {"properties": {"value": round(c.bin_t), "unit": "t", "pct": round(100 * c.bin_t / M.CRUSHER["bin_t"])}},
        "throughput": _v(round(c.tph), "t/h"),
        "crushed": _v(round(c.tonnes), "t"),
        "feed_grade": _v(round(c.bin_grade, 3), "% Cu"),
        "queue": {"properties": {"value": len(c.queue), "unit": "trucks", "trucks": ",".join(c.queue),
                                 "tipping": ",".join(c.tipping)}},
        "starved_time": _v(round(c.starved_s / 60), "min"),
    }
    for name, d in mine.dumps.items():
        out[name] = {
            "received": _v(round(d.tonnes), "t"),
            "copper": _v(round(d.cu_t, 1), "t Cu"),
            "queue": {"properties": {"value": len(d.queue), "unit": "trucks", "tipping": ",".join(d.tipping)}},
        }
    out["fuel"] = {"refueling": _v(sum(1 for t in mine.trucks.values() if t.state in ("refueling", "to_fuel")), "trucks")}

    plan = {}
    for sid in M.SHOVELS:
        assigned = sum(1 for t in mine.trucks.values() if t.status == "up" and t.shovel == sid)
        plan[sid] = assigned
    cycle_by_shovel = {sid: mine._cycle_estimate(sid) for sid in M.SHOVELS}
    # Match factor: truck arrival rate over shovel loading rate (1.0 = balanced).
    match = {}
    for sid in M.SHOVELS:
        if mine.shovels[sid].status != "up":
            continue
        match[sid] = round(plan[sid] * load_time_s(sid) / cycle_by_shovel[sid], 2) if cycle_by_shovel[sid] < 1e5 else 0
    d = mine.last_decision
    out["dispatcher"] = {
        "mode": _v(mine.mode),
        "decisions": _v(mine.decisions, "decisions"),
        "reroutes": _v(mine.reroutes, "reroutes"),
        "last_decision": {"properties": {"value": f"{d.get('truck', '').upper()} → {d.get('shovel', '').upper()}" if d else "",
                                         "travel_min": d.get("travel_min", 0), "wait_min": d.get("wait_min", 0),
                                         "behind_plan_t": d.get("behind_plan_t", 0), "reason": d.get("reason", "")}},
        "allocation": {"properties": {sid: plan[sid] for sid in M.SHOVELS}},
        "match_factor": {"properties": match},
        "log": _v(" | ".join(f"{mine.clock(t)} {text}" for t, text in mine.events[-12:][::-1])),
    }
    if forecast:
        cur, base = forecast["current"], forecast["baseline"]
        out["dispatcher"]["forecast"] = {"properties": {
            "made_at": mine.clock(forecast["at"]),
            **{f"{k}": v for k, v in cur.items()},
            **{f"baseline_{k}": v for k, v in base.items()},
            "loss_cu_t": round(base["cu_t"] - cur["cu_t"], 1),
            "loss_sulphide_t": base["sulphide_crusher_t"] - cur["sulphide_crusher_t"],
            "value": round(cur["cu_t"], 1), "unit": "t Cu by shift end",
        }}
    return out
