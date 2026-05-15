from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple


MeterBucket = Literal["E1", "E2", "B1"]
SourceTag = Literal["user", "default", "derived"]


@dataclass
class DeviceSeriesResult:
    device_name: str
    meter_bucket: MeterBucket
    series_24: List[float]
    source_map: List[Dict[str, str]]
    assumptions: List[str]


def _zeros_24() -> List[float]:
    return [0.0] * 24


def _sum_series_24(series_list: List[List[float]]) -> List[float]:
    out = [0.0] * 24
    for s in series_list:
        for i in range(24):
            out[i] += float(s[i])
    return out


def _scale_to_daily_kwh(shape_24: List[float], daily_kwh: float) -> List[float]:
    total = sum(shape_24)
    if total <= 0:
        return _zeros_24()
    factor = float(daily_kwh) / total
    return [float(x) * factor for x in shape_24]


def _apply_hourly_override(
    base_24: List[float], override: Dict[str, Any], source_map: List[Dict[str, str]]
) -> Tuple[List[float], List[Dict[str, str]], List[str]]:
    assumptions: List[str] = []
    if not isinstance(override, dict):
        return base_24, source_map, assumptions

    hourly = override.get("hourly_kwh_24")
    if isinstance(hourly, list) and len(hourly) == 24:
        out = [float(x) for x in hourly]
        source_map.append({"field": "override.hourly_kwh_24", "source": "user"})
        assumptions.append("Applied absolute hourly override.")
        return out, source_map, assumptions

    windows = override.get("windows")
    daily_kwh = override.get("daily_kwh")

    if isinstance(windows, list) and len(windows) > 0:
        out = _zeros_24()
        for w in windows:
            if not isinstance(w, dict):
                continue
            start = int(w.get("start_hour", 0))
            end = int(w.get("end_hour", 0))
            total_kwh = float(w.get("total_kwh", 0.0))
            start = max(0, min(23, start))
            end = max(1, min(24, end))
            if end <= start or total_kwh <= 0:
                continue
            per_hour = total_kwh / float(end - start)
            for h in range(start, end):
                out[h] += per_hour
        source_map.append({"field": "override.windows", "source": "user"})
        assumptions.append("Built profile from user windows.")
        if daily_kwh is not None:
            out = _scale_to_daily_kwh(out, float(daily_kwh))
            source_map.append({"field": "override.daily_kwh", "source": "user"})
            assumptions.append("Scaled window profile to override.daily_kwh.")
        return out, source_map, assumptions

    if daily_kwh is not None:
        out = _scale_to_daily_kwh(base_24, float(daily_kwh))
        source_map.append({"field": "override.daily_kwh", "source": "user"})
        assumptions.append("Scaled default profile to override.daily_kwh.")
        return out, source_map, assumptions

    return base_24, source_map, assumptions


def _preferred_hour_from_time_of_day(time_of_day: str) -> Optional[int]:
    t = str(time_of_day or "").strip().lower()
    if t in {"overnight", "night"}:
        return 1
    if t in {"morning"}:
        return 7
    if t in {"daytime", "midday"}:
        return 13
    if t in {"evening"}:
        return 20
    return None


def _apply_start_end_window(series_24: List[float], start_hour: int, end_hour: int) -> List[float]:
    start = max(0, min(23, int(start_hour)))
    end = max(1, min(24, int(end_hour)))
    if end <= start:
        return series_24
    total = float(sum(series_24))
    if total <= 0:
        return _zeros_24()
    out = [0.0] * 24
    window_weights = [float(series_24[h]) for h in range(start, end)]
    wsum = float(sum(window_weights))
    if wsum > 0:
        factor = total / wsum
        for i, h in enumerate(range(start, end)):
            out[h] = window_weights[i] * factor
    else:
        per_hour = total / float(end - start)
        for h in range(start, end):
            out[h] = per_hour
    return out


DEFAULT_PROFILES_24: Dict[str, Dict[str, Any]] = {
    "lighting": {
        "bucket": "E1",
        "default_daily_kwh": 1.8,
        "default_quantity": 10,
        "series_24": [
            0.02,
            0.02,
            0.02,
            0.02,
            0.03,
            0.04,
            0.05,
            0.06,
            0.06,
            0.05,
            0.04,
            0.04,
            0.04,
            0.04,
            0.05,
            0.06,
            0.08,
            0.12,
            0.22,
            0.25,
            0.22,
            0.15,
            0.09,
            0.04,
        ],
    },
    "refrigerator": {
        "bucket": "E1",
        "default_daily_kwh": 1.2,
        "default_quantity": 1,
        "series_24": [0.05] * 24,
    },
    "wifi_router": {
        "bucket": "E1",
        "default_daily_kwh": 0.24,
        "default_quantity": 1,
        "series_24": [0.01] * 24,
    },
    "dishwasher": {
        "bucket": "E1",
        "default_daily_kwh": 1.2,
        "default_quantity": 1,
        "series_24": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.2, 0.6, 0.4, 0, 0, 0, 0],
    },
    "dryer": {
        "bucket": "E1",
        "default_daily_kwh": 2.5,
        "default_quantity": 1,
        "series_24": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2.5, 0, 0, 0],
    },
    "induction_cooktop": {
        "bucket": "E1",
        "default_daily_kwh": 1.6,
        "default_quantity": 1,
        "series_24": [0, 0, 0, 0, 0, 0, 0, 0.3, 0.1, 0, 0, 0, 0, 0, 0, 0, 0, 0.2, 0.7, 0.3, 0, 0, 0, 0],
    },
    "pool_pump": {
        "bucket": "E1",
        "default_daily_kwh": 4.0,
        "default_quantity": 1,
        "series_24": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1.0, 1.0, 1.0, 1.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    "air_conditioning": {
        "bucket": "E1",
        "default_daily_kwh": 6.0,
        "default_quantity": 1,
        "series_24": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 0.9, 0.8, 0.7, 0.5, 0.2, 0.1, 0, 0, 0],
    },
    "electric_heating": {
        "bucket": "E1",
        "default_daily_kwh": 5.0,
        "default_quantity": 1,
        "series_24": [0, 0, 0, 0, 0.2, 0.4, 0.6, 0.5, 0.2, 0, 0, 0, 0, 0, 0, 0, 0.2, 0.5, 0.9, 0.8, 0.5, 0.2, 0, 0],
    },
    "ev_charging": {
        "bucket": "E1",
        "default_daily_kwh": 6.0,
        "default_quantity": 1,
        "series_24": [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    "electric_hot_water_controlled_load": {
        "bucket": "E2",
        "default_daily_kwh": 6.0,
        "default_quantity": 1,
        "series_24": [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
}


SOLAR_SHAPE_WEIGHTS_24: List[float] = [
    0,
    0,
    0,
    0,
    0,
    0,
    0.01,
    0.03,
    0.06,
    0.09,
    0.12,
    0.14,
    0.14,
    0.12,
    0.09,
    0.06,
    0.03,
    0.01,
    0,
    0,
    0,
    0,
    0,
    0,
]


def build_device_from_defaults(
    device_type: str,
    *,
    enabled: bool,
    input_params: Optional[Dict[str, Any]] = None,
    override: Optional[Dict[str, Any]] = None,
) -> DeviceSeriesResult:
    spec = DEFAULT_PROFILES_24.get(device_type)
    if not spec:
        return DeviceSeriesResult(
            device_name=device_type,
            meter_bucket="E1",
            series_24=_zeros_24(),
            source_map=[{"field": "device_name", "source": "derived"}],
            assumptions=["Unknown device_name; returned zeros."],
        )
    bucket: MeterBucket = spec["bucket"]
    if not enabled:
        return DeviceSeriesResult(
            device_name=device_type,
            meter_bucket=bucket,
            series_24=_zeros_24(),
            source_map=[{"field": "enabled", "source": "user"}],
            assumptions=["Device disabled; returned zeros."],
        )

    base = [float(x) for x in spec["series_24"]]
    source_map = [{"field": "defaults.series_24", "source": "default"}]
    assumptions = [f"Used default profile for '{device_type}'."]

    params = input_params if isinstance(input_params, dict) else {}

    season = str(params.get("season") or "").strip().lower()
    season_multipliers_by_device: Dict[str, Dict[str, float]] = {
        "lighting": {"summer": 0.90, "autumn": 1.00, "winter": 1.12, "spring": 0.96},
        "air_conditioning": {"summer": 1.25, "autumn": 0.95, "winter": 0.70, "spring": 0.90},
        "electric_heating": {"summer": 0.65, "autumn": 0.95, "winter": 1.35, "spring": 0.85},
    }
    if (
        season in {"summer", "autumn", "winter", "spring"}
        and (override is None or len(override) == 0)
        and params.get("daily_kwh") is None
    ):
        device_multipliers = season_multipliers_by_device.get(device_type, {})
        season_factor = device_multipliers.get(season)
        if season_factor is not None:
            base = [float(x) * float(season_factor) for x in base]
            source_map.append({"field": "input.season", "source": "user"})
            assumptions.append(f"Applied season='{season}' multiplier ({season_factor}) for '{device_type}'.")

    quantity_input = params.get("quantity")
    if quantity_input is not None and (override is None or len(override) == 0) and params.get("daily_kwh") is None:
        try:
            quantity = float(quantity_input)
        except Exception:
            quantity = None
        if quantity is not None and quantity >= 0:
            default_quantity = float(spec.get("default_quantity", 1.0) or 1.0)
            if default_quantity <= 0:
                default_quantity = 1.0
            base = [float(x) * (float(quantity) / default_quantity) for x in base]
            source_map.append({"field": "input.quantity", "source": "user"})
            assumptions.append(
                f"Inferred daily energy from quantity={quantity} "
                f"using default_quantity={default_quantity} for '{device_type}'."
            )

    daily_kwh_input = params.get("daily_kwh")
    if daily_kwh_input is not None and (override is None or len(override) == 0):
        try:
            dki = float(daily_kwh_input)
        except Exception:
            dki = None
        if dki is not None and dki >= 0:
            base = _scale_to_daily_kwh(base, dki)
            source_map.append({"field": "input.daily_kwh", "source": "user"})
            assumptions.append("Scaled default profile to input.daily_kwh.")

    runs_per_week = params.get("runs_per_week")
    if runs_per_week is not None and (override is None or len(override) == 0) and daily_kwh_input is None:
        try:
            rp = float(runs_per_week)
        except Exception:
            rp = None
        if rp is not None and rp >= 0:
            base_total = sum(base)
            target = base_total * (rp / 7.0)
            base = _scale_to_daily_kwh(base, target)
            source_map.append({"field": "input.runs_per_week", "source": "user"})
            assumptions.append("Scaled default daily energy by runs_per_week/7.")

    start_hour = params.get("start_hour")
    end_hour = params.get("end_hour")
    if start_hour is not None and end_hour is not None and (override is None or len(override) == 0):
        try:
            sh = int(start_hour)
            eh = int(end_hour)
        except Exception:
            sh, eh = None, None
        if sh is not None and eh is not None:
            base = _apply_start_end_window(base, sh, eh)
            source_map.append({"field": "input.start_hour", "source": "user"})
            source_map.append({"field": "input.end_hour", "source": "user"})
            assumptions.append("Applied start/end usage window.")

    time_of_day = params.get("time_of_day")
    if time_of_day is not None and (override is None or len(override) == 0) and start_hour is None and end_hour is None:
        preferred_hour = _preferred_hour_from_time_of_day(str(time_of_day))
        if preferred_hour is not None:
            peak = max(base) if base else 0.0
            if peak > 0:
                peak_hour = base.index(peak)
                shift = preferred_hour - peak_hour
                if shift != 0:
                    rotated = [0.0] * 24
                    for i in range(24):
                        rotated[(i + shift) % 24] = float(base[i])
                    base = rotated
                    source_map.append({"field": "input.time_of_day", "source": "user"})
                    assumptions.append(f"Shifted profile to preferred time_of_day='{time_of_day}'.")

    out, source_map, extra = _apply_hourly_override(base, override or {}, source_map)
    assumptions.extend(extra)

    return DeviceSeriesResult(
        device_name=device_type,
        meter_bucket=bucket,
        series_24=out,
        source_map=source_map,
        assumptions=assumptions,
    )


def build_solar_generation(
    *,
    has_solar: bool,
    system_size_kw: Optional[float] = None,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not has_solar:
        return {
            "solar_gen_24": _zeros_24(),
            "meta": {
                "source_map": [{"field": "solar.has_solar", "source": "user"}],
                "assumptions": ["No solar; returned zeros."],
            },
        }

    size_kw = float(system_size_kw) if system_size_kw is not None else 6.6
    daily_gen_kwh = size_kw * 4.0
    base = _scale_to_daily_kwh([float(x) for x in SOLAR_SHAPE_WEIGHTS_24], daily_gen_kwh)
    source_map = [
        {"field": "solar.has_solar", "source": "user"},
        {"field": "solar.system.size_kw", "source": "user"},
    ]
    assumptions = [
        "Used bell-curve solar shape weights.",
        "Scaled solar generation by daily_gen_kwh = size_kw * 4.0 (demo placeholder).",
    ]

    out, source_map, extra = _apply_hourly_override(base, override or {}, source_map)
    assumptions.extend(extra)

    return {
        "solar_gen_24": out,
        "meta": {
            "source_map": source_map,
            "assumptions": assumptions,
        },
    }


def derive_solar_export_b1(
    *,
    e1_24: List[float],
    e2_24: List[float],
    solar_gen_24: List[float],
    export_cap_kw: Optional[float] = None,
) -> List[float]:
    out = [0.0] * 24
    cap = float(export_cap_kw) if export_cap_kw is not None else None
    for h in range(24):
        export = max(0.0, float(solar_gen_24[h]) - (float(e1_24[h]) + float(e2_24[h])))
        if cap is not None:
            export = min(export, cap)
        out[h] = export
    return out


def build_interval_read_day_24(user_input: Dict[str, Any]) -> Dict[str, Any]:
    devices_raw = user_input.get("devices")
    device_instances: List[Dict[str, Any]] = []

    if isinstance(devices_raw, list):
        for item in devices_raw:
            if isinstance(item, dict):
                device_instances.append(item)
    elif isinstance(devices_raw, dict):
        for device_type, cfg in devices_raw.items():
            if isinstance(cfg, bool):
                cfg = {"enabled": cfg}
            elif not isinstance(cfg, dict):
                cfg = {}
            device_instances.append({"id": str(device_type), "type": str(device_type), **cfg})
    else:
        device_instances = []

    breakdown: List[DeviceSeriesResult] = []
    e1_series_list: List[List[float]] = []
    e2_series_list: List[List[float]] = []

    for inst in device_instances:
        device_id = str(inst.get("id") or inst.get("name") or inst.get("type") or "device")
        device_type = str(inst.get("type") or inst.get("device_type") or inst.get("device_name") or "")
        if not device_type:
            continue
        enabled = bool(inst.get("enabled", True))
        input_params = inst.get("input") if isinstance(inst.get("input"), dict) else None
        override = inst.get("override") if isinstance(inst.get("override"), dict) else None

        r = build_device_from_defaults(device_type, enabled=enabled, input_params=input_params, override=override)
        r = DeviceSeriesResult(
            device_name=f"{device_id}:{r.device_name}",
            meter_bucket=r.meter_bucket,
            series_24=r.series_24,
            source_map=r.source_map,
            assumptions=r.assumptions,
        )
        breakdown.append(r)
        if r.meter_bucket == "E1":
            e1_series_list.append(r.series_24)
        elif r.meter_bucket == "E2":
            cl_present = bool(((user_input.get("household") or {}).get("controlled_load_present")))
            if cl_present:
                e2_series_list.append(r.series_24)
            else:
                breakdown.append(
                    DeviceSeriesResult(
                        device_name=f"{device_id}:{device_type} (gated_off)",
                        meter_bucket="E2",
                        series_24=_zeros_24(),
                        source_map=[{"field": "household.controlled_load_present", "source": "user"}],
                        assumptions=["Controlled load not present; E2 gated off (demo policy)."],
                    )
                )

    e1_24 = _sum_series_24(e1_series_list)
    e2_24 = _sum_series_24(e2_series_list)

    solar = user_input.get("solar") or {}
    if not isinstance(solar, dict):
        solar = {}
    solar_out = build_solar_generation(
        has_solar=bool(solar.get("has_solar")),
        system_size_kw=solar.get("system", {}).get("size_kw")
        if isinstance(solar.get("system"), dict)
        else solar.get("system_size_kw"),
        override=solar.get("override") if isinstance(solar.get("override"), dict) else None,
    )

    export_cap_kw = None
    perf = solar.get("performance") if isinstance(solar.get("performance"), dict) else {}
    if isinstance(perf, dict) and perf.get("export_cap_kw") is not None:
        export_cap_kw = float(perf.get("export_cap_kw"))

    b1_24 = derive_solar_export_b1(
        e1_24=e1_24,
        e2_24=e2_24,
        solar_gen_24=solar_out["solar_gen_24"],
        export_cap_kw=export_cap_kw,
    )

    return {
        "E1": {"interval_reads": e1_24, "read_interval_length": 60},
        "E2": {"interval_reads": e2_24, "read_interval_length": 60},
        "B1": {"interval_reads": b1_24, "read_interval_length": 60},
        "solar_generation": {"interval_reads": solar_out["solar_gen_24"], "read_interval_length": 60, **solar_out["meta"]},
        "breakdown": [
            {
                "device_name": d.device_name,
                "meter_bucket": d.meter_bucket,
                "series_24": d.series_24,
                "source_map": d.source_map,
                "assumptions": d.assumptions,
            }
            for d in breakdown
        ],
    }
