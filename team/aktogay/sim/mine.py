"""
Discrete-time simulation of the Aktogay haulage sector and its dispatcher.

Trucks drive the road graph of model.py with physics.py speeds, queue at the
loading units, the crusher bin and the dumps. The dispatcher (module
"Haulage and Dispatch") decides where every empty truck goes next and
forecasts the shift result, with and without the active what-if scenarios.

Pure Python, no I/O: simulator.py publishes the state to the twins, and the
forecast runs on deep copies of this object.
"""

import copy
import heapq
import math
import random

import model as M
from physics import travel, fuel_lph

CLOSED_PENALTY_S = 1e6
PASS_T = {sid: s["bucket_m3"] * M.LOOSE_DENSITY_T_M3 * M.FILL_FACTOR for sid, s in M.SHOVELS.items()}


def passes_needed(sid):
    return math.ceil(M.TRUCK_SPEC["payload_t"] / PASS_T[sid] - 0.15)


def load_time_s(sid):
    return M.SPOT_S + passes_needed(sid) * M.SHOVELS[sid]["cycle_s"]


# ------------------------------------------------------------------ roads
class Roads:
    """Road graph with closures and travel-time routing."""

    def __init__(self):
        self.closed = set()          # road ids
        self.adj = {}
        for e in M.EDGES.values():
            self.adj.setdefault(e["a"], []).append((e["id"], True))
            self.adj.setdefault(e["b"], []).append((e["id"], False))
        self._cache = {}

    def set_closed(self, road, closed):
        (self.closed.add if closed else self.closed.discard)(road)
        self._cache.clear()

    def stranded(self, node):
        """True if every road at this node is closed: the truck may drive out."""
        return not any(self.edge_ok(eid) for eid, _ in self.adj.get(node, ()))

    def edge_ok(self, eid):
        return M.EDGES[eid]["road"] not in self.closed

    @staticmethod
    def leg(eid, forward, loaded):
        e = M.EDGES[eid]
        rise = e["rise"] if forward else -e["rise"]
        mass = M.TRUCK_SPEC["empty_t"] + (M.TRUCK_SPEC["payload_t"] if loaded else 0)
        return travel(mass, rise, e["length"], e["limit_kmh"], loaded)

    def route(self, src, dst, loaded):
        """Fastest open path: (seconds, [(edge, forward), ...]) or (inf, None)."""
        key = (src, dst, loaded)
        if key in self._cache:
            return self._cache[key]
        best = {src: 0.0}
        prev = {}
        heap = [(0.0, src)]
        while heap:
            t, n = heapq.heappop(heap)
            if n == dst:
                break
            if t > best.get(n, math.inf):
                continue
            for eid, fwd in self.adj.get(n, ()):
                e = M.EDGES[eid]
                nxt = e["b"] if fwd else e["a"]
                kmh, _ = self.leg(eid, fwd, loaded)
                # A closed road is used only to drive off it: a truck caught on
                # it when it closed. Paths that need it count as unreachable.
                nt = t + e["length"] / (kmh / 3.6) + (0 if self.edge_ok(eid) else CLOSED_PENALTY_S)
                if nt < best.get(nxt, math.inf):
                    best[nxt] = nt
                    prev[nxt] = (n, eid, fwd)
                    heapq.heappush(heap, (nt, nxt))
        if dst not in best or (best[dst] >= CLOSED_PENALTY_S and not self.stranded(src)):
            result = (math.inf, None)
        else:
            path, n = [], dst
            while n != src:
                p, eid, fwd = prev[n]
                path.append((eid, fwd))
                n = p
            result = (best[dst], path[::-1])
        self._cache[key] = result
        return result


# -------------------------------------------------------------- equipment
class Truck:
    def __init__(self, tid, rng):
        self.id = tid
        self.status = "up"           # what-if: up / down
        self.state = "idle"
        self.node = "j-ne"
        self.path = []               # remaining [(edge, forward)]
        self.edge_pos = 0.0          # metres along path[0]
        self.speed_kmh = 0.0
        self.power_kw = 0.0
        self.payload_t = 0.0
        self.grade = 0.0
        self.material = ""
        self.shovel = None
        self.dest = None
        self.target = None
        self.home = None             # fixed-allocation shovel
        self.timer = 0.0
        self.fuel_l = M.TRUCK_SPEC["tank_l"] * rng.uniform(0.25, 0.95)
        self.factor = rng.uniform(0.95, 1.03)   # operator/condition spread
        self.loads = 0
        self.tonnes = 0.0
        self.fuel_used_l = 0.0
        self.cycle_start = 0.0
        self.last_cycle_min = 0.0
        self.queue_s = 0.0
        self.travel_s = 0.0

    @property
    def loaded(self):
        return self.payload_t > 0

    def xyz(self):
        if not self.path:
            return M.NODES[self.node], 0.0
        eid, fwd = self.path[0]
        e = M.EDGES[eid]
        pts = e["pts"] if fwd else e["pts"][::-1]
        f = min(1.0, self.edge_pos / e["length"]) if e["length"] else 1.0
        # position by fraction of the polyline's segment count (points are evenly spaced)
        x = f * (len(pts) - 1)
        i = min(int(x), len(pts) - 2)
        pa, pb = pts[i], pts[i + 1]
        g = x - i
        pos = tuple(pa[j] + (pb[j] - pa[j]) * g for j in range(3))
        heading = math.degrees(math.atan2(pb[1] - pa[1], pb[0] - pa[0]))
        return pos, heading


class Shovel:
    def __init__(self, sid):
        self.id = sid
        self.spec = M.SHOVELS[sid]
        self.face = self.spec["face"]
        self.status = "up"
        self.queue = []              # truck ids waiting
        self.loading = None          # truck id being loaded
        self.timer = 0.0
        self.truck_passes = 0
        self.passes = 0
        self.tonnes = 0.0
        self.busy_s = 0.0
        self.idle_s = 0.0


class Crusher:
    def __init__(self):
        self.status = "up"
        self.bin_t = 0.0
        self.bin_grade = 0.0
        self.tonnes = 0.0
        self.cu_t = 0.0
        self.tph = 0.0
        self.tipping = []            # truck ids at the bays
        self.queue = []
        self.starved_s = 0.0


class Dump:
    """Waste dump, oxide heap-leach pad or ROM stockpile."""

    def __init__(self, did, bays):
        self.id = did
        self.bays = bays
        self.tipping = []
        self.queue = []
        self.tonnes = 0.0
        self.cu_t = 0.0


# ------------------------------------------------------------------- mine
class Mine:
    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.t = 0.0                  # seconds since shift start
        self.shift = 1
        self.roads = Roads()
        self.mode = "dynamic"         # dispatcher: dynamic / fixed
        self.trucks = {tid: Truck(tid, self.rng) for tid in M.TRUCKS}
        self.shovels = {sid: Shovel(sid) for sid in M.SHOVELS}
        self.crusher = Crusher()
        self.dumps = {"leach": Dump("leach", 3), "dump": Dump("dump", 4), "stockpile": Dump("stockpile", 2)}
        self.face_grade = {fid: f["grade"] for fid, f in M.FACES.items()}
        self.events = []              # (t, text)
        self.decisions = 0
        self.last_decision = {}
        self.reroutes = 0
        self._allocate_fixed()
        # Spread trucks over the cycle so the shift starts mid-operation.
        for i, truck in enumerate(self.trucks.values()):
            truck.node = ["j-ne", "j-se", "re00", "rw00"][i % 4]
            self._dispatch(truck)

    # -- helpers ---------------------------------------------------------
    def log(self, text):
        self.events.append((self.t, text))
        del self.events[:-40]

    def clock(self, t=None):
        s = (8 * 3600 + (self.t if t is None else t)) % 86400
        return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}"

    def _allocate_fixed(self):
        """Fixed allocation from the shift plan: trucks per shovel ~ plan / truck productivity."""
        need = {}
        for sid, sh in self.shovels.items():
            cycle = self._cycle_estimate(sid)
            loads_h = 3600 / cycle
            need[sid] = M.PLAN_TPH[sid] / (M.TRUCK_SPEC["payload_t"] * loads_h)
        counts = {sid: 0 for sid in need}
        for _ in self.trucks:
            sid = max(need, key=lambda s: need[s] - counts[s])
            counts[sid] += 1
        it = iter(self.trucks.values())
        for sid, n in counts.items():
            for _ in range(n):
                next(it).home = sid
        self.fixed_counts = counts
        self.trucks_needed = need

    def _cycle_estimate(self, sid):
        face = self.shovels[sid].face
        dest = M.DESTINATION[M.FACES[face]["material"]]
        go, _ = self.roads.route(dest, face, False)
        back, _ = self.roads.route(face, dest, True)
        return go + back + load_time_s(sid) + M.DUMP_SPOT_S + M.DUMP_S

    # -- what-if interface ---------------------------------------------------
    def set_road(self, road, closed):
        if (road in self.roads.closed) == closed:
            return
        self.roads.set_closed(road, closed)
        self.log(f"Дорога {road}: {'закрыта' if closed else 'открыта'}")
        for truck in self.trucks.values():
            if truck.path and any(not self.roads.edge_ok(e) for e, _ in truck.path[1:]):
                self._reroute(truck)

    def set_shovel(self, sid, status):
        sh = self.shovels[sid]
        if sh.status == status:
            return
        sh.status = status
        self.log(f"{sid.upper()}: {'остановлен' if status == 'down' else 'в работе'}")
        if status == "down" and self.mode == "dynamic":
            waiting = list(sh.queue)
            sh.queue.clear()
            for tid in waiting:
                self._dispatch(self.trucks[tid])
            for truck in self.trucks.values():
                if truck.shovel == sid and truck.state == "to_shovel":
                    self._dispatch(truck)

    def set_truck(self, tid, status):
        truck = self.trucks[tid]
        if truck.status == status:
            return
        truck.status = status
        self.log(f"{tid.upper()}: {'сход с линии' if status == 'down' else 'вернулся на линию'}")
        if status == "down":
            for q in [s.queue for s in self.shovels.values()] + [self.crusher.queue] + \
                     [d.queue for d in self.dumps.values()]:
                if tid in q:
                    q.remove(tid)
            for sh in self.shovels.values():
                if sh.loading == tid:
                    sh.loading = None
            truck.speed_kmh = 0.0
        else:
            if truck.payload_t > 0:
                self._send_loaded(truck)
            else:
                self._dispatch(truck)

    def set_crusher(self, status):
        if self.crusher.status == status:
            return
        self.crusher.status = status
        self.log(f"Дробилка: {'остановлена' if status == 'down' else 'в работе'}")
        if status == "down":
            for tid in list(self.crusher.queue):
                truck = self.trucks[tid]
                self.crusher.queue.remove(tid)
                self._send_loaded(truck)
            for truck in self.trucks.values():
                if truck.dest == "crusher" and truck.state == "hauling":
                    self._send_loaded(truck)

    def set_mode(self, mode):
        if self.mode == mode:
            return
        self.mode = mode
        self.log(f"Диспетчеризация: {'динамическая' if mode == 'dynamic' else 'жёсткое закрепление'}")

    # -- dispatcher -------------------------------------------------------
    def shovel_free_at(self, sh):
        """Time the shovel finishes the trucks already loading or queued there."""
        lt = load_time_s(sh.id)
        busy = 0.0
        if sh.loading:
            busy += max(0.0, lt - sh.timer)
        busy += lt * len(sh.queue)
        return self.t + busy

    def _dispatch(self, truck):
        """Choose the shovel for an empty truck."""
        if truck.status == "down":
            return
        if truck.fuel_l < M.REFUEL_BELOW * M.TRUCK_SPEC["tank_l"] and truck.state != "refueling":
            self._go(truck, "fuel", "to_fuel")
            return
        if self.mode == "fixed":
            sid = truck.home
            self.last_decision = {"truck": truck.id, "shovel": sid, "reason": "закреплён по плану смены"}
        else:
            sid, reason = self._best_shovel(truck)
            if sid is None:
                truck.state, truck.path, truck.speed_kmh = "idle", [], 0.0
                return
            self.last_decision = dict(reason, truck=truck.id, shovel=sid)
            self.decisions += 1
        truck.shovel = sid
        truck.dest = None
        truck.cycle_start = self.t
        self._go(truck, self.shovels[sid].face, "to_shovel")

    def _best_shovel(self, truck):
        elapsed_h = max(self.t, 600.0) / 3600
        en_route = {}
        for t in self.trucks.values():
            if t.state == "to_shovel" and t is not truck:
                en_route[t.shovel] = en_route.get(t.shovel, 0) + 1
        best, best_score, why = None, math.inf, {}
        for sid, sh in self.shovels.items():
            if sh.status != "up":
                continue
            go, path = self.roads.route(truck.node, sh.face, False)
            if path is None:
                continue
            lt = load_time_s(sid)
            arrival = self.t + go
            free = self.shovel_free_at(sh) + lt * en_route.get(sid, 0)
            wait = max(0.0, free - arrival)
            deficit = M.PLAN_TPH[sid] * elapsed_h - sh.tonnes
            # seconds of truck time it is worth to pull a lagging shovel back to plan
            urgency = deficit / M.PLAN_TPH[sid] * 3600 * 0.3
            score = go + wait - urgency
            if score < best_score:
                best, best_score = sid, score
                why = {"travel_min": round(go / 60, 1), "wait_min": round(wait / 60, 1),
                       "behind_plan_t": round(deficit),
                       "reason": f"рейс {go / 60:.1f} мин + ожидание {wait / 60:.1f} мин, "
                                 f"{'отставание' if deficit >= 0 else 'опережение'} плана {abs(deficit):.0f} т"}
        return best, why

    def _send_loaded(self, truck):
        """Destination for a loaded truck; sulphide goes to the stockpile if the crusher is blocked."""
        dest = M.DESTINATION[truck.material] if truck.material else "dump"
        if dest == "crusher":
            blocked = self.crusher.status != "up"
            if self.mode == "dynamic" and not blocked:
                wait = len(self.crusher.queue) * M.TRUCK_SPEC["payload_t"] / M.CRUSHER["rated_tph"] * 3600
                blocked = wait > 12 * 60
            if blocked:
                dest = "stockpile"
                self.log(f"{truck.id.upper()}: руда на рудный склад (дробилка {'стоит' if self.crusher.status != 'up' else 'перегружена'})")
        truck.dest = dest
        self._go(truck, dest, "hauling")

    def _go(self, truck, target, state):
        _, path = self.roads.route(truck.node, target, truck.loaded)
        truck.dest = target if state != "to_shovel" else truck.dest
        truck.state = state
        truck.edge_pos = 0.0
        truck.path = list(path or [])
        truck.target = target
        if path is None:
            truck.state = "blocked"
            truck.speed_kmh = 0.0
        elif not path:
            self._arrive(truck)

    def _reroute(self, truck):
        """Reroute from the end of the current edge."""
        eid, fwd = truck.path[0]
        e = M.EDGES[eid]
        nxt = e["b"] if fwd else e["a"]
        _, path = self.roads.route(nxt, truck.target, truck.loaded)
        if path is None:
            truck.path = truck.path[:1]
        else:
            truck.path = truck.path[:1] + list(path)
            self.reroutes += 1
            self.log(f"{truck.id.upper()}: объезд закрытой дороги")

    # -- step ---------------------------------------------------------------
    def step(self, dt):
        self.t += dt
        for truck in self.trucks.values():
            self._step_truck(truck, dt)
        for sh in self.shovels.values():
            self._step_shovel(sh, dt)
        self._step_crusher(dt)
        for d in self.dumps.values():
            self._step_dump(d, dt)
        for fid in self.face_grade:
            mean = M.FACES[fid]["grade"]
            if mean:
                g = self.face_grade[fid]
                self.face_grade[fid] = max(0.05, g + 0.02 * (mean - g) * dt / 60 + self.rng.gauss(0, 0.004) * math.sqrt(dt / 60))
        if self.t >= M.SHIFT_HOURS * 3600:
            self._new_shift()

    def _new_shift(self):
        self.log(f"Смена {self.shift} закрыта: руда на ЗКД {self.crusher.tonnes:,.0f} т".replace(",", " "))
        self.shift += 1
        self.t = 0.0
        for sh in self.shovels.values():
            sh.tonnes = sh.busy_s = sh.idle_s = 0.0
            sh.passes = 0
        self.crusher.tonnes = self.crusher.cu_t = self.crusher.starved_s = 0.0
        for d in self.dumps.values():
            d.tonnes = d.cu_t = 0.0
        for t in self.trucks.values():
            t.loads, t.tonnes, t.fuel_used_l, t.queue_s, t.travel_s = 0, 0.0, 0.0, 0.0, 0.0

    def _step_truck(self, truck, dt):
        if truck.status == "down":
            truck.speed_kmh, truck.power_kw = 0.0, 0.0
            return
        moving = truck.state in ("to_shovel", "hauling", "to_fuel") and truck.path
        if not moving:
            truck.speed_kmh = 0.0
            truck.power_kw = 0.0 if truck.state in ("refueling",) else 0.05 * M.TRUCK_SPEC["engine_kw"]
            if truck.state in ("queued_shovel", "queued_dump"):
                truck.queue_s += dt
            if truck.state == "refueling":
                truck.timer += dt
                if truck.timer >= M.REFUEL_S:
                    truck.fuel_l = M.TRUCK_SPEC["tank_l"]
                    self._dispatch(truck)
            elif truck.state in ("idle", "blocked") and truck.payload_t == 0:
                if int(self.t) % 30 < dt:
                    self._dispatch(truck)
            elif truck.state == "blocked" and truck.payload_t > 0:
                if int(self.t) % 30 < dt:
                    self._send_loaded(truck)
            truck.fuel_l -= fuel_lph(truck.power_kw) * dt / 3600
            truck.fuel_used_l += fuel_lph(truck.power_kw) * dt / 3600
            return
        remaining = dt
        while remaining > 0 and truck.path:
            eid, fwd = truck.path[0]
            e = M.EDGES[eid]
            kmh, kw = Roads.leg(eid, fwd, truck.loaded)
            kmh *= truck.factor
            v = kmh / 3.6
            left = e["length"] - truck.edge_pos
            t_edge = left / v
            used = min(remaining, t_edge)
            truck.edge_pos += v * used
            truck.speed_kmh, truck.power_kw = kmh, kw
            burn = fuel_lph(kw) * used / 3600
            truck.fuel_l -= burn
            truck.fuel_used_l += burn
            truck.travel_s += used
            remaining -= used
            if used >= t_edge - 1e-9:
                truck.node = e["b"] if fwd else e["a"]
                truck.path.pop(0)
                truck.edge_pos = 0.0
                if truck.path and not self.roads.edge_ok(truck.path[0][0]):
                    _, path = self.roads.route(truck.node, truck.target, truck.loaded)
                    if path is None:
                        truck.path = []
                        truck.state = "blocked"
                        return
                    truck.path = list(path)
                    self.reroutes += 1
        if not truck.path:
            self._arrive(truck)

    def _arrive(self, truck):
        truck.speed_kmh = 0.0
        if truck.state == "to_shovel":
            sh = self.shovels[truck.shovel]
            truck.state = "queued_shovel"
            sh.queue.append(truck.id)
        elif truck.state == "hauling":
            truck.state = "queued_dump"
            if truck.dest == "crusher":
                self.crusher.queue.append(truck.id)
            else:
                self.dumps[truck.dest].queue.append(truck.id)
        elif truck.state == "to_fuel":
            truck.state = "refueling"
            truck.timer = 0.0

    def _step_shovel(self, sh, dt):
        if sh.status != "up":
            sh.idle_s += dt
            return
        if sh.loading is None:
            sh.queue = [tid for tid in sh.queue if self.trucks[tid].status == "up"]
            if not sh.queue:
                sh.idle_s += dt
                return
            sh.loading = sh.queue.pop(0)
            sh.timer = 0.0
            sh.truck_passes = 0
            truck = self.trucks[sh.loading]
            truck.state = "loading"
            truck.material = M.FACES[sh.face]["material"]
        truck = self.trucks[sh.loading]
        sh.busy_s += dt
        sh.timer += dt
        n = passes_needed(sh.id)
        due = min(n, int(max(0.0, sh.timer - M.SPOT_S) // sh.spec["cycle_s"]))
        while sh.truck_passes < due:
            # Last pass tops the truck up to rated payload, +-5 % operator spread.
            room = M.TRUCK_SPEC["payload_t"] * self.rng.uniform(0.95, 1.05) - truck.payload_t
            bucket = max(0.0, min(PASS_T[sh.id] * self.rng.uniform(0.92, 1.05), room))
            g = max(0.0, self.face_grade[sh.face] * self.rng.lognormvariate(0, 0.08)) if M.FACES[sh.face]["grade"] else 0.0
            if bucket:
                truck.grade = (truck.grade * truck.payload_t + g * bucket) / (truck.payload_t + bucket)
                truck.payload_t += bucket
            sh.truck_passes += 1
            sh.passes += 1
        if sh.truck_passes >= n:
            sh.tonnes += truck.payload_t
            sh.loading = None
            self._send_loaded(truck)

    def _step_crusher(self, dt):
        c = self.crusher
        if c.status == "up":
            out = min(c.bin_t, M.CRUSHER["rated_tph"] * dt / 3600)
            c.bin_t -= out
            c.tonnes += out
            c.cu_t += out * c.bin_grade / 100
            c.tph = 0.9 * c.tph + 0.1 * (out * 3600 / dt)
            if c.bin_t < 1:
                c.starved_s += dt
            c.queue = [tid for tid in c.queue if self.trucks[tid].status == "up"]
            while c.queue and len(c.tipping) < M.CRUSHER["bays"]:
                truck = self.trucks[c.queue[0]]
                tipping_t = sum(self.trucks[t].payload_t for t in c.tipping)
                if c.bin_t + tipping_t + truck.payload_t > M.CRUSHER["bin_t"]:
                    break
                c.queue.pop(0)
                c.tipping.append(truck.id)
                truck.state, truck.timer = "dumping", 0.0
        else:
            c.tph = 0.9 * c.tph
        for tid in list(c.tipping):
            truck = self.trucks[tid]
            truck.timer += dt
            if truck.timer >= M.DUMP_SPOT_S + M.DUMP_S:
                c.tipping.remove(tid)
                c.bin_grade = (c.bin_grade * c.bin_t + truck.grade * truck.payload_t) / (c.bin_t + truck.payload_t)
                c.bin_t += truck.payload_t
                self._unload(truck)

    def _step_dump(self, d, dt):
        d.queue = [tid for tid in d.queue if self.trucks[tid].status == "up"]
        while d.queue and len(d.tipping) < d.bays:
            tid = d.queue.pop(0)
            d.tipping.append(tid)
            self.trucks[tid].state, self.trucks[tid].timer = "dumping", 0.0
        for tid in list(d.tipping):
            truck = self.trucks[tid]
            truck.timer += dt
            if truck.timer >= M.DUMP_SPOT_S + M.DUMP_S:
                d.tipping.remove(tid)
                d.tonnes += truck.payload_t
                d.cu_t += truck.payload_t * truck.grade / 100
                self._unload(truck)

    def _unload(self, truck):
        truck.loads += 1
        truck.tonnes += truck.payload_t
        truck.payload_t, truck.grade, truck.material = 0.0, 0.0, ""
        truck.last_cycle_min = (self.t - truck.cycle_start) / 60 if truck.cycle_start <= self.t else 0.0
        self._dispatch(truck)

    # -- planning ---------------------------------------------------------
    def forecast(self, dt=5.0):
        """Run copies to the end of the shift: as is, and with every what-if undone."""
        end = M.SHIFT_HOURS * 3600

        def run(mine):
            while mine.t + dt < end and mine.shift == self.shift:
                mine.step(dt)
            return mine.totals()

        current = run(copy.deepcopy(self))
        base = copy.deepcopy(self)
        for road in list(base.roads.closed):
            base.roads.set_closed(road, False)
        for sh in base.shovels.values():
            sh.status = "up"
        for t in base.trucks.values():
            if t.status == "down":
                t.status = "up"
                base._dispatch(t)
        base.crusher.status = "up"
        base.mode = "dynamic"
        baseline = run(base)
        return {"at": self.t, "current": current, "baseline": baseline}

    def totals(self):
        return {
            "sulphide_crusher_t": round(self.crusher.tonnes + self.crusher.bin_t),
            "sulphide_stockpile_t": round(self.dumps["stockpile"].tonnes),
            "oxide_t": round(self.dumps["leach"].tonnes),
            "waste_t": round(self.dumps["dump"].tonnes),
            "cu_t": round(self.crusher.cu_t + self.dumps["stockpile"].cu_t + self.dumps["leach"].cu_t, 1),
        }
