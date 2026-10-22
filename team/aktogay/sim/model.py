"""
Static model of the Aktogay haulage sector: pit geometry, road graph,
equipment specifications and the shift plan.

Everything the simulator, the twins and the 3D scene need to agree on lives
here, so there is one source of truth. Coordinates are metres in a local
frame: x east, y north, z elevation relative to the pit rim.

Sources of the numbers are listed in SUBMISSION.md, "Data and Assumptions".
Pit geometry is schematic (KAZ Minerals does not publish the pit shell); its
scale follows the published production rates and typical large copper pits.
"""

import math

NAMESPACE = "kz.aktogay"

# --------------------------------------------------------------- pit shell
BENCH_HEIGHT_M = 15.0          # typical for large copper open pits
BENCHES = 16                   # pit bottom at -240 m
RIM_A_M = 1000.0               # rim ellipse semi-axis east-west
RIM_B_M = 700.0                # rim ellipse semi-axis north-south
BENCH_STEP_M = 17.9            # horizontal step per bench: ~40 deg overall slope
RAMP_GRADE = 0.08              # 8 % haul ramp grade
RAMP_SEG_M = BENCH_HEIGHT_M / RAMP_GRADE   # 187.5 m of ramp per bench


def bench_z(k):
    return -BENCH_HEIGHT_M * k


def bench_axes(k):
    return RIM_A_M - BENCH_STEP_M * k, RIM_B_M - BENCH_STEP_M * k


def ellipse_point(k, theta):
    a, b = bench_axes(k)
    return (a * math.cos(theta), b * math.sin(theta), bench_z(k))


def _advance(k, theta, arc_m, direction=1):
    """Angle reached after walking arc_m metres along bench k's ellipse."""
    a, b = bench_axes(k)
    walked, step = 0.0, 0.002 * direction
    while walked < arc_m:
        dx = -a * math.sin(theta) * step
        dy = b * math.cos(theta) * step
        walked += math.hypot(dx, dy)
        theta += step
    return theta


# ----------------------------------------------------------------- graph
# Nodes: id -> (x, y, z). Edges: id -> dict(a, b, length, grade, road, limit).
NODES = {}
EDGES = {}


def _node(nid, xyz):
    NODES[nid] = tuple(round(v, 1) for v in xyz)


def _arc_pts(k0, k1, t0, t1, step_m=40.0):
    """Polyline along the pit wall from (bench k0, parameter t0) to (k1, t1)."""
    a0, b0 = bench_axes(k0)
    n = max(2, int(abs(t1 - t0) * (a0 + b0) / 2 / step_m) + 1)
    pts = []
    for i in range(n + 1):
        f = i / n
        k = k0 + (k1 - k0) * f
        a, b = bench_axes(k)
        t = t0 + (t1 - t0) * f
        pts.append((round(a * math.cos(t), 1), round(b * math.sin(t), 1), round(bench_z(k), 1)))
    return pts


def _edge(a, b, road, limit_kmh, length=None, pts=None):
    ax, ay, az = NODES[a]
    bx, by, bz = NODES[b]
    horiz = length if length is not None else math.hypot(bx - ax, by - ay)
    eid = f"{a}~{b}"
    EDGES[eid] = {
        "id": eid, "a": a, "b": b, "road": road,
        "length": round(math.hypot(horiz, bz - az), 1),
        "rise": round(bz - az, 1),          # metres climbed going a -> b
        "limit_kmh": limit_kmh,
        "pts": pts or [NODES[a], NODES[b]],   # drawn and driven polyline
    }


# Two spiral ramps, half a turn apart, each 187.5 m per bench at 8 %.
RAMPS = {"e": 0.0, "w": math.pi}
for ramp, theta0 in RAMPS.items():
    theta, prev = theta0, None
    for k in range(BENCHES + 1):
        _node(f"r{ramp}{k:02d}", ellipse_point(k, theta))
        if k:
            road = f"ramp-{ramp}-{'upper' if k <= 8 else 'lower'}"
            _edge(f"r{ramp}{k - 1:02d}", f"r{ramp}{k:02d}", road, 40, length=RAMP_SEG_M,
                  pts=_arc_pts(k - 1, k, prev, theta))
        prev = theta
        theta = _advance(k + 1 if k < BENCHES else k, theta, RAMP_SEG_M)

# Working benches: face between the two ramps on the same bench.
# face id -> bench, angle offset from ramp E node, material, mean grade % Cu
FACES = {
    "face-b04": {"bench": 4, "offset": 0.55, "material": "oxide", "grade": 0.27,
                 "name": "Забой Б-04 (окисленная руда)"},
    "face-b07": {"bench": 7, "offset": 0.65, "material": "waste", "grade": 0.0,
                 "name": "Забой Б-07 (вскрыша)"},
    "face-b10": {"bench": 10, "offset": 0.60, "material": "sulphide", "grade": 0.36,
                 "name": "Забой Б-10 (сульфидная руда)"},
    "face-b13": {"bench": 13, "offset": 0.70, "material": "sulphide", "grade": 0.31,
                 "name": "Забой Б-13 (сульфидная руда)"},
}


def _arc_len(k, t0, t1):
    a, b = bench_axes(k)
    n = 200
    total = 0.0
    for i in range(n):
        ta = t0 + (t1 - t0) * i / n
        tb = t0 + (t1 - t0) * (i + 1) / n
        pa = (a * math.cos(ta), b * math.sin(ta))
        pb = (a * math.cos(tb), b * math.sin(tb))
        total += math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    return total


def _param_of(nid, k):
    """Ellipse parameter of a node lying on bench k."""
    a, b = bench_axes(k)
    x, y, _ = NODES[nid]
    return math.atan2(y / b, x / a)


for fid, face in FACES.items():
    k = face["bench"]
    te = _param_of(f"re{k:02d}", k)
    tw = _param_of(f"rw{k:02d}", k)
    tf = te + face["offset"]
    # walk from E ramp node counter-clockwise through the face to the W ramp node
    while tw < tf:
        tw += 2 * math.pi
    _node(fid, ellipse_point(k, tf))
    road = f"bench-{k:02d}"
    _edge(f"re{k:02d}", fid, road, 30, length=_arc_len(k, te, tf), pts=_arc_pts(k, k, te, tf))
    _edge(fid, f"rw{k:02d}", road, 30, length=_arc_len(k, tf, tw), pts=_arc_pts(k, k, tf, tw))
    face["node"] = fid

# Surface: ring road around the rim and the destinations.
_node("j-ne", (720, 820, 0))
_node("j-nw", (-720, 820, 0))
_node("j-se", (720, -820, 0))
_node("j-sw", (-720, -820, 0))
_node("crusher", (0, 1150, 8))
_node("stockpile", (380, 1230, 4))
_node("leach", (2350, 380, 22))
_node("dump", (0, -1750, 45))
_node("fuel", (-1250, 1050, 0))

for a, b, road in [
    ("re00", "j-ne", "haul-north"), ("rw00", "j-nw", "haul-north"),
    ("j-ne", "crusher", "haul-north"), ("j-nw", "crusher", "haul-north"),
    ("j-ne", "stockpile", "haul-north"),
    ("re00", "j-se", "haul-south"), ("rw00", "j-sw", "haul-south"),
    ("j-se", "dump", "haul-south"), ("j-sw", "dump", "haul-south"),
    ("re00", "leach", "haul-east"), ("j-ne", "leach", "haul-east"),
    ("j-nw", "fuel", "service"),
]:
    _edge(a, b, road, 50)

ROADS = {
    "ramp-e-upper": "Съезд Восточный, верх (гор. 0…−120)",
    "ramp-e-lower": "Съезд Восточный, низ (гор. −120…−240)",
    "ramp-w-upper": "Съезд Западный, верх (гор. 0…−120)",
    "ramp-w-lower": "Съезд Западный, низ (гор. −120…−240)",
    "bench-04": "Транспортная берма гор. −60",
    "bench-07": "Транспортная берма гор. −105",
    "bench-10": "Транспортная берма гор. −150",
    "bench-13": "Транспортная берма гор. −195",
    "haul-north": "Автодорога к дробилке и рудному складу",
    "haul-south": "Автодорога к отвалу вскрыши",
    "haul-east": "Автодорога к штабелю кучного выщелачивания",
    "service": "Подъезд к топливозаправщику",
}

# ------------------------------------------------------------- equipment
# CAT 793F mining truck (manufacturer specifications).
TRUCK_SPEC = {
    "model": "CAT 793F",
    "payload_t": 226.8,          # rated payload
    "empty_t": 163.3,            # GMW 390 t minus rated payload
    "engine_kw": 1976.0,         # C175-16 gross power
    "drive_eff": 0.75,           # engine-to-rimpull efficiency (mechanical drive)
    "top_kmh": 60.0,
    "tank_l": 4354.0,
    "sfc_l_per_kwh": 0.25,       # ~210 g/kWh diesel at 0.84 kg/l
    "idle_l_per_h": 25.0,
}
ROLLING_RESISTANCE = 0.03       # well-maintained haul road, CAT Performance Handbook
LOADED_DOWNHILL_KMH = 30.0      # retarder-limited speed on ramps

# Loading units. Pass payload = bucket m3 * loose density * fill factor.
LOOSE_DENSITY_T_M3 = 1.85        # blasted porphyry copper ore, swell ~1.4
SHOVELS = {
    "ex-01": {"model": "Hitachi EX5600-6", "bucket_m3": 29.0, "cycle_s": 32.0, "face": "face-b10",
              "name": "Экскаватор EX-01"},
    "ex-02": {"model": "Hitachi EX5600-6", "bucket_m3": 29.0, "cycle_s": 32.0, "face": "face-b13",
              "name": "Экскаватор EX-02"},
    "ex-03": {"model": "Hitachi EX5600-6", "bucket_m3": 29.0, "cycle_s": 32.0, "face": "face-b07",
              "name": "Экскаватор EX-03"},
    "wl-01": {"model": "CAT 994K", "bucket_m3": 19.0, "cycle_s": 40.0, "face": "face-b04",
              "name": "Погрузчик WL-01"},
}
FILL_FACTOR = 0.9
SPOT_S = 40.0                    # truck spotting under the shovel
DUMP_S = 60.0                    # tipping
DUMP_SPOT_S = 30.0

# Primary gyratory crusher feeding a 25 Mt/y sulphide concentrator:
# 25e6 t / (8760 h * 0.92 availability) ~ 3100 t/h.
CRUSHER = {"rated_tph": 3100.0, "bin_t": 700.0, "bays": 2}   # dump pocket ~3 truckloads

TRUCKS = [f"ht-{i:02d}" for i in range(1, 15)]
REFUEL_BELOW = 0.15
REFUEL_S = 900.0

# Shift plan, t/h per loading unit (12 h shift).
SHIFT_HOURS = 12
PLAN_TPH = {"ex-01": 1800.0, "ex-02": 1500.0, "ex-03": 1800.0, "wl-01": 1000.0}

# Destination by material. Sulphide goes to the stockpile when the crusher
# cannot take it (rehandled later by a loader).
DESTINATION = {"sulphide": "crusher", "oxide": "leach", "waste": "dump"}
