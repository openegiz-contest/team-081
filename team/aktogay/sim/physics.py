"""
Haul truck travel physics: speed from engine power and resistance, fuel from
the power actually used. Rimpull-limited speed is how the CAT Performance
Handbook gradeability curves are read; this is the same balance in closed form.
"""

from model import TRUCK_SPEC, ROLLING_RESISTANCE, LOADED_DOWNHILL_KMH

G = 9.81


def travel(mass_t, rise_m, length_m, limit_kmh, loaded):
    """Return (speed km/h, engine power kW) for one road segment.

    Grade resistance is rise/length; total resistance adds rolling resistance.
    Uphill the speed is where the available rimpull power balances the
    resistance force; it is capped by the road limit and the top gear.
    """
    spec = TRUCK_SPEC
    grade = rise_m / length_m if length_m else 0.0
    resistance = grade + ROLLING_RESISTANCE
    cap = min(limit_kmh, spec["top_kmh"])
    if grade < -0.02:
        # Downhill: retarder-limited, loaded trucks slower.
        cap = min(cap, LOADED_DOWNHILL_KMH if loaded else cap)
    if resistance <= 0:
        return cap, 0.08 * spec["engine_kw"]
    force_n = mass_t * 1000 * G * resistance
    rim_w = spec["engine_kw"] * 1000 * spec["drive_eff"]
    v_ms = min(rim_w / force_n, cap / 3.6)
    power_kw = force_n * v_ms / spec["drive_eff"] / 1000
    return v_ms * 3.6, min(power_kw, spec["engine_kw"])


def fuel_lph(power_kw):
    return max(TRUCK_SPEC["idle_l_per_h"], power_kw * TRUCK_SPEC["sfc_l_per_kwh"])
