#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_CHECKER_FILE = ROOT / "volta-guradian" / "scenario_checker" / "get_scenario.py"
INTERVAL_BUILDER_FILE = ROOT / "interval-builder-demo" / "interval_builder.py"
INTERVAL_DATA_SYNTH_FILE = (
    ROOT / "volta-guradian" / "interval_data" / "src" / "interval_data_engine" / "synthetic_interval_generator.py"
)

DEVICE_TYPES_ALL = [
    "lighting",
    "refrigerator",
    "wifi_router",
    "air_conditioning",
    "electric_heating",
    "pool_pump",
    "dishwasher",
    "dryer",
    "induction_cooktop",
    "ev_charging",
    "electric_hot_water_controlled_load",
]

DEVICE_QUANTITY_BASELINES: Dict[str, Dict[str, float]] = {
    "lighting": {"default_daily_kwh": 1.8, "default_quantity": 10.0},
    "refrigerator": {"default_daily_kwh": 1.2, "default_quantity": 1.0},
    "wifi_router": {"default_daily_kwh": 0.24, "default_quantity": 1.0},
    "air_conditioning": {"default_daily_kwh": 6.0, "default_quantity": 1.0},
    "electric_heating": {"default_daily_kwh": 5.0, "default_quantity": 1.0},
    "pool_pump": {"default_daily_kwh": 4.0, "default_quantity": 1.0},
    "dishwasher": {"default_daily_kwh": 1.2, "default_quantity": 1.0},
    "dryer": {"default_daily_kwh": 2.5, "default_quantity": 1.0},
    "induction_cooktop": {"default_daily_kwh": 1.6, "default_quantity": 1.0},
    "ev_charging": {"default_daily_kwh": 6.0, "default_quantity": 1.0},
    "electric_hot_water_controlled_load": {"default_daily_kwh": 6.0, "default_quantity": 1.0},
}


def _load_function(py_file: Path, fn_name: str):
    module_name = f"mod_{py_file.stem}_{fn_name}"
    spec = importlib.util.spec_from_file_location(module_name, str(py_file))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {py_file}")
    mod = importlib.util.module_from_spec(spec)
    # Needed for dataclass/type resolution in modules loaded from file paths.
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    fn = getattr(mod, fn_name, None)
    if fn is None:
        raise RuntimeError(f"Function '{fn_name}' not found in {py_file}")
    return fn


def _seed_from_inputs(postcode: str, retailer: str, seed: Optional[int]) -> int:
    if seed is not None:
        return int(seed)
    s = f"{postcode}|{retailer}"
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def _pick(rng: Random, items: List[Any]) -> Any:
    return items[rng.randrange(len(items))]


def _parse_bool_text(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _has_hint_path(hints: Dict[str, Any], path: List[str]) -> bool:
    cur: Any = hints
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur.get(part)
    return True


def _replace_profile_with_non_solar(profile: Dict[str, Any]) -> None:
    scenario_hint = str(profile.get("scenario_hint") or "")
    if "SOLAR" in scenario_hint.upper():
        profile["scenario_hint"] = "PLAN ONLY"
    # profile_id is derived later from resolved attributes; keep archetype_id stable.


def _ensure_default_solar(profile: Dict[str, Any]) -> None:
    solar = profile.get("solar")
    if not isinstance(solar, dict):
        solar = {}
        profile["solar"] = solar
    solar["has_solar"] = True
    if not isinstance(solar.get("system"), dict):
        solar["system"] = {"size_kw": 6.6}
    solar_system = solar.get("system") if isinstance(solar.get("system"), dict) else {}
    if solar_system.get("size_kw") is None:
        solar_system["size_kw"] = 6.6
    if not isinstance(solar.get("performance"), dict):
        solar["performance"] = {"export_cap_kw": 5.0, "estimated_export_ratio": 0.45, "self_consumption_ratio": 0.55}
    plan = profile.get("plan")
    if isinstance(plan, dict):
        plan["solar_fit_enabled"] = True
        if plan.get("fit_rate") is None:
            plan["fit_rate"] = 0.07


def _apply_hard_constraints(profile: Dict[str, Any], user_hints: Dict[str, Any], user_hints_raw: Dict[str, Any]) -> Dict[str, Any]:
    profile = deepcopy(profile)
    anchored_mode = bool(user_hints_raw)

    solar_flag = None
    if _has_hint_path(user_hints, ["solar", "has_solar"]):
        solar_flag = bool(((user_hints.get("solar") or {}).get("has_solar")))
    if solar_flag is False:
        profile.pop("solar", None)
        plan = profile.get("plan")
        if isinstance(plan, dict):
            plan["solar_fit_enabled"] = False
            plan.pop("fit_rate", None)
        _replace_profile_with_non_solar(profile)
    elif solar_flag is True:
        _ensure_default_solar(profile)

    battery_flag = None
    if _has_hint_path(user_hints, ["battery", "has_battery"]):
        battery_flag = bool(((user_hints.get("battery") or {}).get("has_battery")))
    elif anchored_mode:
        battery_flag = False
    if battery_flag is False:
        profile.pop("battery", None)
    elif battery_flag is True:
        battery = profile.get("battery")
        if not isinstance(battery, dict):
            profile["battery"] = {
                "has_battery": True,
                "system": {"capacity_kwh": 13.5, "usable_capacity_kwh": 12.0, "coupling": "ac_coupled"},
            }
        else:
            battery["has_battery"] = True

    ev_flag = None
    if _has_hint_path(user_hints, ["ev", "has_ev"]):
        ev_flag = bool(((user_hints.get("ev") or {}).get("has_ev")))
    elif anchored_mode:
        ev_flag = False
    if ev_flag is False:
        profile.pop("ev", None)
    elif ev_flag is True:
        ev = profile.get("ev")
        if not isinstance(ev, dict):
            profile["ev"] = {
                "has_ev": True,
                "usage": {"annual_km": 12000},
                "charging": {"pattern": "overnight", "location": "home", "home_charging_share": 1.0},
            }
        else:
            ev["has_ev"] = True

    # Postcode plausibility prior (lightweight) for Melbourne CBD 3000, only when user didn't explicitly set these.
    postcode = str((((profile.get("location") or {}).get("postcode")) or "")).strip()
    if postcode == "3000":
        household = profile.get("household")
        if isinstance(household, dict):
            if not _has_hint_path(user_hints_raw, ["household", "type"]):
                if str(household.get("type") or "") == "Detached":
                    household["type"] = "Apartment"
            if not _has_hint_path(user_hints_raw, ["household", "phase_connection"]):
                household["phase_connection"] = "single_phase"
            if not _has_hint_path(user_hints_raw, ["household", "lifestyle_signals", "has_pool"]):
                lifestyle = household.get("lifestyle_signals")
                if isinstance(lifestyle, dict):
                    lifestyle["has_pool"] = False
                appliances = household.get("appliances")
                if isinstance(appliances, dict):
                    appliances["pool_pump"] = False
    return profile


def _scenario_slug(scenario_hint: str) -> str:
    mapping = {
        "PLAN ONLY": "plan-only",
        "PLAN + SOLAR (EXIST)": "solar-existing",
        "PLAN + SOLAR (NEW)": "solar-new",
        "PLAN + SOLAR + BATTERY + EV": "battery-ev",
    }
    return mapping.get(str(scenario_hint or "").strip(), "profile")


def _people_slug(people_count: Any) -> str:
    s = str(people_count).strip().lower() if people_count is not None else ""
    if s in {"4+", "4_plus", "4plus"}:
        return "5p-plus"
    try:
        n = int(float(s)) if s else None
    except Exception:
        n = None
    if n is None or n <= 0:
        return "unknown"
    if n >= 5:
        return "5p-plus"
    return f"{n}p"


def _occupancy_slug(occupancy_pattern: Any) -> str:
    if not isinstance(occupancy_pattern, list):
        occupancy_pattern = []
    occ = {str(x).strip() for x in occupancy_pattern if x is not None}
    # Family signal should win over evening-heavy if both are present (more informative label).
    if "school_term_pattern" in occ:
        return "family"
    if "weekday_daytime_home" in occ:
        return "daytime"
    if "weekday_evening_only" in occ:
        return "evening"
    return "mixed"


def _usage_band_slug(monthly_usage_kwh: Any) -> str:
    try:
        kwh = float(monthly_usage_kwh)
    except Exception:
        return "unknown"
    if kwh < 450:
        return "low"
    if kwh <= 650:
        return "medium"
    return "high"


def _build_profile_label(profile: Dict[str, Any]) -> str:
    household = profile.get("household") if isinstance(profile.get("household"), dict) else {}
    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    solar = profile.get("solar") if isinstance(profile.get("solar"), dict) else {}
    device_inputs = profile.get("device_inputs") if isinstance(profile.get("device_inputs"), dict) else {}

    scenario = _scenario_slug(str(profile.get("scenario_hint") or ""))
    dwelling = str(household.get("type") or "unknown").strip().lower().replace(" ", "-")
    people = _people_slug(household.get("people_count"))
    occupancy = _occupancy_slug(household.get("occupancy_pattern"))
    usage = _usage_band_slug(energy.get("monthly_usage_kwh"))

    suffixes: List[str] = []
    if bool(solar.get("has_solar")):
        suffixes.append("solar")
    heat_cfg = device_inputs.get("electric_heating")
    if isinstance(heat_cfg, dict) and bool(heat_cfg.get("enabled")):
        suffixes.append("heating")
    cl_cfg = device_inputs.get("electric_hot_water_controlled_load")
    if isinstance(cl_cfg, dict) and bool(cl_cfg.get("enabled")):
        suffixes.append("cl")

    parts = [scenario, dwelling, people, occupancy, usage]
    parts.extend(suffixes)
    return "-".join([p for p in parts if p])


def _set_profile_identity(profile: Dict[str, Any]) -> None:
    # template_id: stable name of the original template/archetype used to create this candidate
    if profile.get("template_id") is None:
        profile["template_id"] = profile.get("profile_id")

    # profile_id: purely technical id, stable within this run (do not encode business semantics)
    opt = profile.get("candidate_index")
    try:
        opt_i = int(opt) if opt is not None else None
    except Exception:
        opt_i = None
    if opt_i is None:
        profile["profile_id"] = "prof_unknown"
    else:
        profile["profile_id"] = f"prof_{opt_i:03d}"

    # profile_label: human-readable semantic label (derived from resolved attributes)
    profile["profile_label"] = _build_profile_label(profile)


def _estimate_monthly_bill_from_profile_interval(profile: Dict[str, Any], compact_result: Dict[str, Any]) -> float:
    e1_daily = float((compact_result.get("E1") or {}).get("daily_kwh", 0.0))
    e2_daily = float((compact_result.get("E2") or {}).get("daily_kwh", 0.0))
    b1_daily = float((compact_result.get("B1") or {}).get("daily_kwh", 0.0))
    solar_daily = float((compact_result.get("solar_generation") or {}).get("daily_kwh", 0.0))

    solar_self_used = max(0.0, solar_daily - b1_daily)
    e1_net = max(0.0, e1_daily - solar_self_used)
    e2_net = max(0.0, e2_daily)

    plan = profile.get("plan") if isinstance(profile.get("plan"), dict) else {}
    tariff_type = str(plan.get("tariff_type") or "").upper()
    if tariff_type in {"TIME_OF_USE", "TIME_OF_USE_CONT_LOAD"}:
        e1_rate = 0.33
        e2_rate = 0.24
    elif tariff_type in {"SINGLE_RATE", "SINGLE_RATE_CONT_LOAD"}:
        e1_rate = 0.29
        e2_rate = 0.22
    else:
        e1_rate = 0.30
        e2_rate = 0.22
    fit_rate = float(plan.get("fit_rate") or 0.07)
    if not bool(((profile.get("solar") or {}).get("has_solar"))):
        fit_rate = 0.0
    daily_supply = 1.05
    monthly_days = 30.0

    est = ((e1_net * e1_rate) + (e2_net * e2_rate) - (b1_daily * fit_rate) + daily_supply) * monthly_days
    return round(max(0.0, est), 2)


def _scale_profile_device_intensity(profile: Dict[str, Any], factor: float) -> None:
    device_inputs = profile.get("device_inputs")
    if not isinstance(device_inputs, dict):
        return
    factor = max(0.40, min(1.90, float(factor)))
    core_devices = {"lighting", "refrigerator", "wifi_router"}
    continuous_scale_devices = {
        "lighting",
        "air_conditioning",
        "electric_heating",
        "pool_pump",
        "induction_cooktop",
        "ev_charging",
        "electric_hot_water_controlled_load",
    }
    for device_type, cfg in device_inputs.items():
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled")):
            continue
        inp = cfg.get("input")
        if not isinstance(inp, dict):
            continue

        damped_factor = factor
        if device_type in {"refrigerator", "wifi_router"}:
            damped_factor = 1.0 + ((factor - 1.0) * 0.35)

        if inp.get("daily_kwh") is not None:
            try:
                v = float(inp.get("daily_kwh"))
                inp["daily_kwh"] = round(max(0.0, v * damped_factor), 3)
            except Exception:
                pass
        if inp.get("quantity") is not None:
            try:
                q = float(inp.get("quantity"))
                q2 = int(round(q * damped_factor))
                if q2 < 1:
                    if device_type in core_devices:
                        q2 = 1
                    else:
                        q2 = 0
                        cfg["enabled"] = False
                inp["quantity"] = q2
            except Exception:
                pass
        if inp.get("runs_per_week") is not None:
            try:
                r = float(inp.get("runs_per_week"))
                r2 = int(round(r * damped_factor))
                if r2 < 1:
                    if device_type in core_devices:
                        r2 = 1
                    else:
                        r2 = 0
                inp["runs_per_week"] = max(0, min(14, r2))
            except Exception:
                pass
        if inp.get("daily_kwh") is None and device_type in continuous_scale_devices:
            spec = DEVICE_QUANTITY_BASELINES.get(device_type, {})
            base_daily = float(spec.get("default_daily_kwh", 0.0) or 0.0)
            qty = float(inp.get("quantity", 1) or 0)
            if qty <= 0 and device_type not in core_devices:
                cfg["enabled"] = False
            if base_daily > 0 and (qty > 0 or device_type in core_devices):
                default_qty = float(spec.get("default_quantity", 1.0) or 1.0)
                if default_qty <= 0:
                    default_qty = 1.0
                scaled_daily = base_daily * (max(0.15, damped_factor)) * (max(0.0, qty) / default_qty)
                inp["daily_kwh"] = round(max(0.0, scaled_daily), 3)


def _calibrate_profile_to_targets(profile: Dict[str, Any], build_interval_fn, user_hints: Dict[str, Any]) -> Dict[str, Any]:
    profile = deepcopy(profile)
    energy_hints = user_hints.get("energy_usage") if isinstance(user_hints.get("energy_usage"), dict) else {}
    target_bill = energy_hints.get("monthly_bill")
    target_usage = energy_hints.get("monthly_usage_kwh")

    if target_bill is None and target_usage is None:
        return profile

    for _ in range(5):
        interval_input = profile_to_interval_input(profile)
        interval_result = build_interval_fn(interval_input)
        compact_result = _compact_interval_result(interval_result)
        est_bill = _estimate_monthly_bill_from_profile_interval(profile, compact_result)
        e1_daily = float((compact_result.get("E1") or {}).get("daily_kwh", 0.0))
        e2_daily = float((compact_result.get("E2") or {}).get("daily_kwh", 0.0))
        est_usage = (e1_daily + e2_daily) * 30.0

        factor = 1.0
        if target_bill is not None and est_bill > 0:
            try:
                factor *= float(target_bill) / float(est_bill)
            except Exception:
                pass
        if target_usage is not None and est_usage > 0:
            try:
                factor *= float(target_usage) / float(est_usage)
            except Exception:
                pass
        factor = max(0.30, min(2.20, factor))
        if abs(factor - 1.0) < 0.03:
            break
        _scale_profile_device_intensity(profile, factor)

    energy = profile.get("energy_usage")
    if not isinstance(energy, dict):
        energy = {}
        profile["energy_usage"] = energy
    if target_bill is not None:
        try:
            energy["monthly_bill"] = round(float(target_bill), 2)
        except Exception:
            pass
    if target_usage is not None:
        try:
            energy["monthly_usage_kwh"] = round(float(target_usage), 1)
        except Exception:
            pass
    elif target_bill is not None and energy.get("monthly_usage_kwh") is None:
        try:
            energy["monthly_usage_kwh"] = _estimate_monthly_usage_from_bill(float(target_bill))
        except Exception:
            pass
    return profile


def _reconcile_profile_consistency(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = deepcopy(profile)
    household = profile.get("household") if isinstance(profile.get("household"), dict) else {}
    lifestyle = household.get("lifestyle_signals") if isinstance(household.get("lifestyle_signals"), dict) else {}
    appliances = household.get("appliances") if isinstance(household.get("appliances"), dict) else {}
    ev = profile.get("ev") if isinstance(profile.get("ev"), dict) else {}
    solar = profile.get("solar") if isinstance(profile.get("solar"), dict) else {}
    battery = profile.get("battery") if isinstance(profile.get("battery"), dict) else {}
    device_inputs = profile.get("device_inputs") if isinstance(profile.get("device_inputs"), dict) else {}

    has_pool = bool(lifestyle.get("has_pool")) or bool(appliances.get("pool_pump"))
    if isinstance(device_inputs.get("pool_pump"), dict):
        device_inputs["pool_pump"]["enabled"] = has_pool
    has_hot_water = bool(lifestyle.get("has_electric_hot_water"))
    if isinstance(device_inputs.get("electric_hot_water_controlled_load"), dict):
        device_inputs["electric_hot_water_controlled_load"]["enabled"] = has_hot_water
    has_ev = bool(ev.get("has_ev") or ev.get("planning_ev"))
    if isinstance(device_inputs.get("ev_charging"), dict):
        device_inputs["ev_charging"]["enabled"] = has_ev
    profile["device_inputs"] = device_inputs

    has_solar = bool(solar.get("has_solar"))
    has_battery = bool(battery.get("has_battery"))
    if has_solar:
        if has_battery or has_ev:
            profile["scenario_hint"] = "PLAN + SOLAR (EXIST)"
        else:
            profile["scenario_hint"] = "PLAN + SOLAR (EXIST)"
    else:
        profile["scenario_hint"] = "PLAN ONLY"

    return profile

def _estimate_monthly_usage_from_bill(monthly_bill: float) -> float:
    # Demo heuristic, AU-like blended retail rate assumption.
    rate = 0.28
    return round(max(50.0, float(monthly_bill) / rate), 1)


def _estimate_monthly_bill_from_usage(monthly_usage_kwh: float) -> float:
    rate = 0.28
    return round(max(30.0, float(monthly_usage_kwh) * rate), 2)


def _window_for_device(device_type: str, time_of_day: Optional[str] = None) -> Dict[str, int]:
    tod = str(time_of_day or "").strip().lower()
    if device_type == "lighting":
        return {"start_hour": 18, "end_hour": 23}
    if device_type == "refrigerator":
        return {"start_hour": 0, "end_hour": 24}
    if device_type == "wifi_router":
        return {"start_hour": 0, "end_hour": 24}
    if device_type == "air_conditioning":
        if tod == "evening":
            return {"start_hour": 18, "end_hour": 23}
        return {"start_hour": 12, "end_hour": 18}
    if device_type == "electric_heating":
        if tod == "morning":
            return {"start_hour": 6, "end_hour": 9}
        return {"start_hour": 17, "end_hour": 22}
    if device_type == "pool_pump":
        return {"start_hour": 10, "end_hour": 16}
    if device_type == "dishwasher":
        return {"start_hour": 19, "end_hour": 21}
    if device_type == "dryer":
        return {"start_hour": 20, "end_hour": 22}
    if device_type == "induction_cooktop":
        return {"start_hour": 17, "end_hour": 20}
    if device_type == "ev_charging":
        return {"start_hour": 0, "end_hour": 6}
    if device_type == "electric_hot_water_controlled_load":
        return {"start_hour": 0, "end_hour": 6}
    return {"start_hour": 0, "end_hour": 24}


def _load_user_hints(args: argparse.Namespace) -> Dict[str, Any]:
    hints: Dict[str, Any] = {}
    if args.user_inputs:
        raw = json.loads(Path(args.user_inputs).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit("--user-inputs must point to a JSON object.")
        hints = deepcopy(raw)

    if args.monthly_bill is not None:
        hints.setdefault("energy_usage", {})["monthly_bill"] = float(args.monthly_bill)
    if args.monthly_usage_kwh is not None:
        hints.setdefault("energy_usage", {})["monthly_usage_kwh"] = float(args.monthly_usage_kwh)
    if args.people_count is not None:
        hints.setdefault("household", {})["people_count"] = args.people_count
    if args.household_type is not None:
        hints.setdefault("household", {})["type"] = args.household_type
    if args.ownership is not None:
        hints.setdefault("household", {})["ownership"] = args.ownership
    if args.wfh_days is not None:
        hints.setdefault("household", {})["work_from_home_days"] = int(args.wfh_days)
    if args.month_of_bill is not None:
        hints.setdefault("energy_usage", {})["month_of_bill"] = args.month_of_bill
    if args.occupancy_pattern is not None:
        pattern = [p.strip() for p in str(args.occupancy_pattern).split(",") if p.strip()]
        if pattern:
            hints.setdefault("household", {})["occupancy_pattern"] = pattern

    has_solar = _parse_bool_text(args.has_solar)
    has_battery = _parse_bool_text(args.has_battery)
    has_ev = _parse_bool_text(args.has_ev)
    if has_solar is not None:
        hints.setdefault("solar", {})["has_solar"] = has_solar
    if has_battery is not None:
        hints.setdefault("battery", {})["has_battery"] = has_battery
    if has_ev is not None:
        hints.setdefault("ev", {})["has_ev"] = has_ev

    return hints


def _apply_user_hints_to_profile(profile: Dict[str, Any], user_hints: Dict[str, Any], rng: Random) -> Dict[str, Any]:
    if not user_hints:
        return profile
    profile_hints = deepcopy(user_hints)
    device_hints = profile_hints.pop("device_inputs", None)
    profile = _deep_merge(profile, profile_hints)

    # Keep monthly bill/usage coherent if user gave only one anchor.
    energy = profile.get("energy_usage")
    if not isinstance(energy, dict):
        energy = {}
        profile["energy_usage"] = energy
    bill = energy.get("monthly_bill")
    usage = energy.get("monthly_usage_kwh")
    if bill is not None and usage is None:
        try:
            energy["monthly_usage_kwh"] = _estimate_monthly_usage_from_bill(float(bill))
        except Exception:
            pass
    if usage is not None and bill is None:
        try:
            energy["monthly_bill"] = _estimate_monthly_bill_from_usage(float(usage))
        except Exception:
            pass

    # Simple consistency for children signal after people_count overrides.
    household = profile.get("household")
    if isinstance(household, dict):
        people_count = household.get("people_count")
        if household.get("has_children") is None:
            try:
                pc = int(str(people_count).replace("+", "").strip())
                household["has_children"] = pc >= 3
            except Exception:
                pass

    # Rebuild device defaults from updated profile context.
    profile["device_inputs"] = _build_device_inputs(profile, rng)
    if isinstance(device_hints, dict):
        profile["device_inputs"] = _deep_merge(profile["device_inputs"], device_hints)
    return profile

def _people_count_to_band(people_count: Any) -> str:
    # Returns one of: "1", "2_3", "4_plus"
    if isinstance(people_count, str):
        s = people_count.strip().lower()
        if s in {"4+", "4_plus", "4plus"}:
            return "4_plus"
        try:
            n = int(s)
        except Exception:
            n = None
    elif isinstance(people_count, (int, float)):
        n = int(people_count)
    else:
        n = None
    if n is None:
        return "2_3"
    if n <= 1:
        return "1"
    if n <= 3:
        return "2_3"
    return "4_plus"


def _month_from_profile(profile: Dict[str, Any]) -> int:
    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    month_of_bill = energy.get("month_of_bill")
    if isinstance(month_of_bill, str) and len(month_of_bill) >= 7 and month_of_bill[4] == "-":
        try:
            m = int(month_of_bill[5:7])
            if 1 <= m <= 12:
                return m
        except Exception:
            pass
    return date.today().month


def _season_from_month_au(month: int) -> str:
    if month in (12, 1, 2):
        return "summer"
    if month in (3, 4, 5):
        return "autumn"
    if month in (6, 7, 8):
        return "winter"
    return "spring"


def _baseline_daily_kwh_for_household_devices(people_band: str, wfh_days: int, month: int) -> Dict[str, float]:
    # Simple heuristics for always-on/common devices.
    base = {
        "1": {"lighting": 1.2, "refrigerator": 1.0, "wifi_router": 0.20},
        "2_3": {"lighting": 1.8, "refrigerator": 1.2, "wifi_router": 0.25},
        "4_plus": {"lighting": 2.5, "refrigerator": 1.4, "wifi_router": 0.32},
    }[people_band]
    season = _season_from_month_au(month)
    season_lighting_multiplier = {
        "summer": 0.90,  # longer daylight
        "autumn": 1.00,
        "winter": 1.12,  # shorter daylight
        "spring": 0.96,
    }[season]
    wfh_factor = max(0.0, min(1.0, float(wfh_days) / 5.0))
    # More at-home time tends to increase lighting + wifi usage modestly.
    lighting = round(base["lighting"] * season_lighting_multiplier * (1.0 + 0.18 * wfh_factor), 3)
    refrigerator = round(base["refrigerator"] * (1.0 + 0.04 * wfh_factor), 3)
    wifi_router = round(base["wifi_router"] * (1.0 + 0.20 * wfh_factor), 3)
    return {"lighting": lighting, "refrigerator": refrigerator, "wifi_router": wifi_router}


def _size_adjusted_profile_inputs(people_band: str, wfh_days: int, month: int, rng: Random) -> Dict[str, Any]:
    # Size-aware defaults for selected appliances.
    if people_band == "1":
        dishwasher_choices = [2, 3, 4]
        dryer_choices = [1, 2, 3]
        cooktop_base = 1.0
        hot_water_base = 4.5
        aircon_base = 4.2
        heating_base = 3.8
    elif people_band == "2_3":
        dishwasher_choices = [4, 5, 6]
        dryer_choices = [2, 3, 4]
        cooktop_base = 1.6
        hot_water_base = 6.0
        aircon_base = 6.0
        heating_base = 5.2
    else:
        dishwasher_choices = [6, 7, 8]
        dryer_choices = [3, 4, 5]
        cooktop_base = 2.4
        hot_water_base = 8.0
        aircon_base = 8.2
        heating_base = 7.0

    wfh_factor = max(0.0, min(1.0, float(wfh_days) / 5.0))
    season = _season_from_month_au(month)
    season_multipliers = {
        "summer": {"ac": 1.25, "heat": 0.65},
        "autumn": {"ac": 0.95, "heat": 0.95},
        "winter": {"ac": 0.70, "heat": 1.35},
        "spring": {"ac": 0.90, "heat": 0.85},
    }[season]

    # More WFH slightly increases cooking/hot-water intensity.
    cooktop_daily = round(cooktop_base * (1.0 + 0.12 * wfh_factor), 3)
    hot_water_daily = round(hot_water_base * (1.0 + 0.08 * wfh_factor), 3)
    aircon_daily = round(aircon_base * (1.0 + 0.18 * wfh_factor) * season_multipliers["ac"], 3)
    heating_daily = round(heating_base * (1.0 + 0.12 * wfh_factor) * season_multipliers["heat"], 3)

    # Optional WFH nudge for runs/week.
    dish_runs = _pick(rng, dishwasher_choices)
    dryer_runs = _pick(rng, dryer_choices)
    if wfh_days >= 4:
        dish_runs = min(dish_runs + 1, 9)
        dryer_runs = min(dryer_runs + 1, 6)

    return {
        "dishwasher_runs_per_week": dish_runs,
        "dryer_runs_per_week": dryer_runs,
        "cooktop_daily_kwh": cooktop_daily,
        "hot_water_daily_kwh": hot_water_daily,
        "air_conditioning_daily_kwh": aircon_daily,
        "electric_heating_daily_kwh": heating_daily,
        "season": season,
        "season_multiplier_ac": season_multipliers["ac"],
        "season_multiplier_heating": season_multipliers["heat"],
    }


def _infer_quantity_from_daily_kwh(device_type: str, target_daily_kwh: float, *, min_qty: int = 1, max_qty: int = 50) -> int:
    spec = DEVICE_QUANTITY_BASELINES.get(device_type)
    if not spec:
        return min_qty
    base_daily = float(spec.get("default_daily_kwh", 1.0) or 1.0)
    base_qty = float(spec.get("default_quantity", 1.0) or 1.0)
    if base_daily <= 0:
        return min_qty
    raw_qty = float(target_daily_kwh) * base_qty / base_daily
    qty = int(round(raw_qty))
    if float(target_daily_kwh) > 0 and qty < 1:
        qty = 1
    return max(min_qty, min(max_qty, qty))


def _build_device_inputs(profile: Dict[str, Any], rng: Random) -> Dict[str, Dict[str, Any]]:
    household = profile.get("household") if isinstance(profile.get("household"), dict) else {}
    appliances = household.get("appliances") if isinstance(household.get("appliances"), dict) else {}
    lifestyle = household.get("lifestyle_signals") if isinstance(household.get("lifestyle_signals"), dict) else {}
    ev = profile.get("ev") if isinstance(profile.get("ev"), dict) else {}
    charging = ev.get("charging") if isinstance(ev.get("charging"), dict) else {}
    people_band = _people_count_to_band(household.get("people_count"))
    try:
        wfh_days = int(household.get("work_from_home_days", 0) or 0)
    except Exception:
        wfh_days = 0
    month = _month_from_profile(profile)
    season = _season_from_month_au(month)
    baseline = _baseline_daily_kwh_for_household_devices(people_band, wfh_days, month)
    size_adjusted = _size_adjusted_profile_inputs(people_band, wfh_days, month, rng)

    # Always include all handled devices so samples are explicit.
    device_inputs: Dict[str, Dict[str, Any]] = {k: {"enabled": False} for k in DEVICE_TYPES_ALL}

    # Typical always-on household devices.
    device_inputs["lighting"] = {
        "enabled": True,
        "input": {
            "time_of_day": "evening",
            "season": season,
            "quantity": _infer_quantity_from_daily_kwh("lighting", baseline["lighting"], min_qty=2, max_qty=40),
            **_window_for_device("lighting", "evening"),
        },
    }
    device_inputs["refrigerator"] = {
        "enabled": True,
        "input": {
            "quantity": _infer_quantity_from_daily_kwh("refrigerator", baseline["refrigerator"], min_qty=1, max_qty=3),
            **_window_for_device("refrigerator"),
        },
    }
    device_inputs["wifi_router"] = {
        "enabled": True,
        "input": {
            "quantity": _infer_quantity_from_daily_kwh("wifi_router", baseline["wifi_router"], min_qty=1, max_qty=4),
            **_window_for_device("wifi_router"),
        },
    }

    if bool(lifestyle.get("has_air_conditioning")):
        ac_tod = _pick(rng, ["daytime", "evening"])
        device_inputs["air_conditioning"] = {
            "enabled": True,
            "input": {
                "time_of_day": ac_tod,
                "season": size_adjusted["season"],
                "quantity": _infer_quantity_from_daily_kwh(
                    "air_conditioning", size_adjusted["air_conditioning_daily_kwh"], min_qty=1, max_qty=5
                ),
                **_window_for_device("air_conditioning", ac_tod),
            },
        }
    if bool(lifestyle.get("has_electric_heating")):
        heat_tod = _pick(rng, ["morning", "evening"])
        device_inputs["electric_heating"] = {
            "enabled": True,
            "input": {
                "time_of_day": heat_tod,
                "season": size_adjusted["season"],
                "quantity": _infer_quantity_from_daily_kwh(
                    "electric_heating", size_adjusted["electric_heating_daily_kwh"], min_qty=1, max_qty=5
                ),
                **_window_for_device("electric_heating", heat_tod),
            },
        }
    if bool(lifestyle.get("has_pool")) or bool(appliances.get("pool_pump")):
        device_inputs["pool_pump"] = {
            "enabled": True,
            "input": {"time_of_day": "daytime", "quantity": 1, **_window_for_device("pool_pump", "daytime")},
        }
    if bool(appliances.get("dishwasher")):
        device_inputs["dishwasher"] = {
            "enabled": True,
            "input": {
                "quantity": 1,
                "runs_per_week": size_adjusted["dishwasher_runs_per_week"],
                "time_of_day": "evening",
                **_window_for_device("dishwasher", "evening"),
            },
        }
    if bool(appliances.get("dryer")):
        device_inputs["dryer"] = {
            "enabled": True,
            "input": {
                "quantity": 1,
                "runs_per_week": size_adjusted["dryer_runs_per_week"],
                "time_of_day": "evening",
                **_window_for_device("dryer", "evening"),
            },
        }
    if bool(appliances.get("induction_cooktop")):
        device_inputs["induction_cooktop"] = {
            "enabled": True,
            "input": {
                "time_of_day": "evening",
                "quantity": _infer_quantity_from_daily_kwh(
                    "induction_cooktop", size_adjusted["cooktop_daily_kwh"], min_qty=1, max_qty=3
                ),
                **_window_for_device("induction_cooktop", "evening"),
            },
        }
    if bool(lifestyle.get("has_electric_hot_water")):
        device_inputs["electric_hot_water_controlled_load"] = {
            "enabled": True,
            "input": {
                "time_of_day": "overnight",
                "quantity": _infer_quantity_from_daily_kwh(
                    "electric_hot_water_controlled_load", size_adjusted["hot_water_daily_kwh"], min_qty=1, max_qty=3
                ),
                **_window_for_device("electric_hot_water_controlled_load", "overnight"),
            },
        }

    if bool(ev.get("has_ev") or ev.get("planning_ev")):
        pattern = str(charging.get("pattern") or "").lower()
        if "day" in pattern:
            tod = "daytime"
        elif "evening" in pattern:
            tod = "evening"
        else:
            tod = "overnight"
        ev_input: Dict[str, Any] = {"time_of_day": tod, **_window_for_device("ev_charging", tod)}
        if charging.get("home_charging_share") is not None:
            ev_input["home_charging_share"] = charging.get("home_charging_share")
        ev_input["quantity"] = 1
        device_inputs["ev_charging"] = {"enabled": True, "input": ev_input}

    return device_inputs


def generate_candidate_profiles(postcode: str, retailer: str, rng: Random) -> List[Dict[str, Any]]:
    # Shared value pools (randomized per run but deterministic by seed).
    plan_names = ["Saver Flex", "Everyday", "Balance Plan", "Home Plus", "EV Saver"]
    occupancy_sets = {
        "single_worker": [
            ["weekday_evening_only", "public_holiday_away", "summer_low_occupancy"],
            ["weekday_evening_only", "winter_high_occupancy"],
        ],
        "couple_mixed": [
            ["weekday_daytime_home", "public_holiday_home"],
            ["weekday_evening_only", "work_from_home_pattern"],
        ],
        "family_4_plus": [
            ["weekday_evening_only", "school_term_pattern", "public_holiday_home"],
            ["weekday_daytime_home", "school_term_pattern", "winter_high_occupancy"],
        ],
    }

    p1 = {
        "profile_id": "plan_only_single_worker_1p",
        "scenario_hint": "PLAN ONLY",
        "location": {"postcode": postcode},
        "plan": {
            "retailer_name": retailer,
            "current_plan_name": _pick(rng, plan_names),
            "eligibility": {"has_smart_meter": _pick(rng, [False, True])},
        },
        "energy_usage": {"monthly_bill": _pick(rng, [120, 140, 160, 180]), "month_of_bill": _pick(rng, ["2026-01", "2026-04", "2026-07", "2026-10"])},
        "household": {
            "type": _pick(rng, ["Apartment", "Townhouse"]),
            "ownership": _pick(rng, ["rent", "own"]),
            "people_count": 1,
            "occupancy_pattern": _pick(rng, occupancy_sets["single_worker"]),
            "work_from_home_days": _pick(rng, [0, 1, 2]),
            "has_children": False,
            "phase_connection": "single_phase",
            "controlled_load_present": False,
            "lifestyle_signals": {
                "has_pool": False,
                "has_electric_hot_water": _pick(rng, [False, True]),
                "has_air_conditioning": True,
                "has_electric_heating": _pick(rng, [False, True]),
            },
            "appliances": {
                "dryer": _pick(rng, [False, True]),
                "dishwasher": _pick(rng, [True, False]),
                "pool_pump": False,
                "induction_cooktop": _pick(rng, [False, True]),
            },
        },
    }

    p2 = {
        "profile_id": "plan_only_couple_2_3p_mixed",
        "scenario_hint": "PLAN ONLY",
        "location": {"postcode": postcode},
        "plan": {
            "retailer_name": retailer,
            "current_plan_name": _pick(rng, plan_names),
            "eligibility": {"has_smart_meter": _pick(rng, [True, False])},
        },
        "energy_usage": {"monthly_usage_kwh": _pick(rng, [360, 420, 520, 610]), "month_of_bill": _pick(rng, ["2026-02", "2026-05", "2026-08", "2026-11"])},
        "household": {
            "type": _pick(rng, ["Apartment", "Townhouse", "Detached"]),
            "ownership": _pick(rng, ["rent", "own"]),
            "people_count": _pick(rng, [2, 3]),
            "occupancy_pattern": _pick(rng, occupancy_sets["couple_mixed"]),
            "work_from_home_days": _pick(rng, [1, 2, 3]),
            "has_children": _pick(rng, [False, True]),
            "phase_connection": _pick(rng, ["single_phase", "three_phase"]),
            "controlled_load_present": _pick(rng, [False, True]),
            "lifestyle_signals": {
                "has_pool": False,
                "has_electric_hot_water": _pick(rng, [True, False]),
                "has_air_conditioning": True,
                "has_electric_heating": _pick(rng, [False, True]),
            },
            "appliances": {
                "dryer": True,
                "dishwasher": True,
                "pool_pump": False,
                "induction_cooktop": _pick(rng, [True, False]),
            },
        },
    }

    p3 = {
        "profile_id": "plan_only_family_4_plus",
        "scenario_hint": "PLAN ONLY",
        "location": {"postcode": postcode},
        "plan": {
            "retailer_name": retailer,
            "current_plan_name": _pick(rng, plan_names),
            "eligibility": {"has_smart_meter": _pick(rng, [True, False])},
        },
        "energy_usage": {"monthly_usage_kwh": _pick(rng, [720, 820, 920]), "month_of_bill": _pick(rng, ["2026-01", "2026-04", "2026-07", "2026-10"])},
        "household": {
            "type": "Detached",
            "ownership": "own",
            "people_count": "4+",
            "occupancy_pattern": _pick(rng, occupancy_sets["family_4_plus"]),
            "work_from_home_days": _pick(rng, [0, 1, 2]),
            "has_children": True,
            "phase_connection": _pick(rng, ["single_phase", "three_phase"]),
            "controlled_load_present": True,
            "lifestyle_signals": {
                "has_pool": _pick(rng, [True, False]),
                "has_electric_hot_water": True,
                "has_air_conditioning": True,
                "has_electric_heating": _pick(rng, [True, False]),
            },
            "appliances": {
                "dryer": True,
                "dishwasher": True,
                "pool_pump": _pick(rng, [True, False]),
                "induction_cooktop": True,
            },
        },
    }

    p4 = {
        "profile_id": "plan_solar_existing_wfh",
        "scenario_hint": "PLAN + SOLAR (EXIST)",
        "location": {"postcode": postcode},
        "plan": {
            "retailer_name": retailer,
            "current_plan_name": _pick(rng, plan_names),
            "eligibility": {"has_smart_meter": True},
            "solar_fit_enabled": True,
            "fit_rate": _pick(rng, [0.05, 0.07, 0.1]),
        },
        "energy_usage": {"monthly_usage_kwh": _pick(rng, [550, 620, 700]), "month_of_bill": _pick(rng, ["2026-02", "2026-05", "2026-08", "2026-11"])},
        "household": {
            "type": _pick(rng, ["Townhouse", "Detached"]),
            "ownership": "own",
            "people_count": _pick(rng, [2, 3]),
            "occupancy_pattern": ["weekday_daytime_home", "public_holiday_home"],
            "work_from_home_days": _pick(rng, [3, 4, 5]),
            "has_children": _pick(rng, [False, True]),
            "phase_connection": _pick(rng, ["single_phase", "three_phase"]),
            "controlled_load_present": _pick(rng, [True, False]),
            "lifestyle_signals": {
                "has_pool": _pick(rng, [False, True]),
                "has_electric_hot_water": True,
                "has_air_conditioning": True,
                "has_electric_heating": _pick(rng, [False, True]),
            },
            "appliances": {
                "dryer": True,
                "dishwasher": True,
                "pool_pump": _pick(rng, [False, True]),
                "induction_cooktop": True,
            },
        },
        "solar": {
            "has_solar": True,
            "system": {
                "size_kw": _pick(rng, [5.0, 6.6, 8.0]),
                "inverter_brand": _pick(rng, ["Sungrow", "Fronius", "SolarEdge"]),
                "inverter_model": _pick(rng, ["SG5K-D", "Primo 5.0-1", "SE5000H"]),
                "inverter_phase": _pick(rng, ["single_phase", "three_phase"]),
                "install_year": _pick(rng, [2021, 2022, 2023]),
            },
            "performance": {
                "estimated_export_ratio": _pick(rng, [0.35, 0.45, 0.55]),
                "self_consumption_ratio": _pick(rng, [0.45, 0.55, 0.65]),
                "export_cap_kw": _pick(rng, [3.5, 5.0]),
            },
            "site_conditions": {
                "orientation": _pick(rng, ["north", "north_east", "north_west"]),
                "tilt_deg": _pick(rng, [20, 25, 30]),
                "shading_level": _pick(rng, ["low", "medium"]),
            },
        },
    }

    p5 = {
        "profile_id": "plan_solar_battery_ev_family",
        "scenario_hint": "PLAN + SOLAR + BATTERY + EV",
        "location": {"postcode": postcode},
        "plan": {
            "retailer_name": retailer,
            "current_plan_name": _pick(rng, plan_names),
            "eligibility": {"has_smart_meter": True},
            "solar_fit_enabled": True,
            "fit_rate": _pick(rng, [0.05, 0.07, 0.1]),
        },
        "energy_usage": {"monthly_usage_kwh": _pick(rng, [760, 860, 960]), "month_of_bill": _pick(rng, ["2026-01", "2026-04", "2026-07", "2026-10"])},
        "household": {
            "type": "Detached",
            "ownership": "own",
            "people_count": "4+",
            "occupancy_pattern": _pick(rng, occupancy_sets["family_4_plus"]),
            "work_from_home_days": _pick(rng, [1, 2, 3]),
            "has_children": True,
            "phase_connection": "three_phase",
            "controlled_load_present": True,
            "lifestyle_signals": {
                "has_pool": _pick(rng, [True, False]),
                "has_electric_hot_water": True,
                "has_air_conditioning": True,
                "has_electric_heating": _pick(rng, [True, False]),
            },
            "appliances": {
                "dryer": True,
                "dishwasher": True,
                "pool_pump": _pick(rng, [True, False]),
                "induction_cooktop": True,
            },
        },
        "solar": {
            "has_solar": True,
            "system": {
                "size_kw": _pick(rng, [6.6, 8.0, 10.0]),
                "inverter_brand": _pick(rng, ["Sungrow", "Fronius", "SolarEdge"]),
                "inverter_model": _pick(rng, ["SG8K-D", "Primo 6.0-1", "SE7600H"]),
                "inverter_phase": "three_phase",
                "install_year": _pick(rng, [2022, 2023, 2024]),
            },
            "performance": {
                "estimated_export_ratio": _pick(rng, [0.4, 0.5, 0.6]),
                "self_consumption_ratio": _pick(rng, [0.4, 0.5, 0.6]),
                "export_cap_kw": _pick(rng, [5.0, 7.0]),
            },
        },
        "battery": {
            "has_battery": True,
            "system": {
                "capacity_kwh": _pick(rng, [10.0, 13.5, 15.0]),
                "usable_capacity_kwh": _pick(rng, [9.0, 12.0, 13.0]),
                "brand": _pick(rng, ["Tesla", "Sungrow", "BYD"]),
                "model": _pick(rng, ["Powerwall 2", "SBR", "Battery-Box"]),
                "install_year": _pick(rng, [2023, 2024, 2025]),
                "coupling": _pick(rng, ["ac_coupled", "dc_coupled"]),
            },
            "features": {"backup_capability": _pick(rng, [True, False]), "health_estimate": _pick(rng, ["good", "fair"])},
        },
        "ev": {
            "has_ev": True,
            "usage": {"annual_km": _pick(rng, [12000, 15000, 18000])},
            "charging": {
                "charger_type": _pick(rng, ["level_2", "home_ac"]),
                "location": "home",
                "pattern": _pick(rng, ["overnight", "off_peak"]),
                "flexible": True,
                "home_charging_share": _pick(rng, [0.8, 0.9, 1.0]),
            },
        },
    }

    profiles = [p1, p2, p3, p4, p5]
    for i, profile in enumerate(profiles):
        profile["candidate_index"] = i + 1
        profile["template_id"] = profile.get("profile_id")
        profile["device_inputs"] = _build_device_inputs(profile, rng)
        _set_profile_identity(profile)
    return profiles


def _summary(profile: Dict[str, Any]) -> Dict[str, Any]:
    household = profile.get("household") or {}
    energy = profile.get("energy_usage") or {}
    solar = profile.get("solar") or {}
    battery = profile.get("battery") or {}
    ev = profile.get("ev") or {}
    device_inputs = profile.get("device_inputs") if isinstance(profile.get("device_inputs"), dict) else {}
    enabled_devices = sum(1 for _k, v in device_inputs.items() if isinstance(v, dict) and bool(v.get("enabled")))
    devices_compact: Dict[str, Dict[str, Any]] = {}
    for device_type in DEVICE_TYPES_ALL:
        cfg = device_inputs.get(device_type, {"enabled": False})
        if isinstance(cfg, bool):
            cfg = {"enabled": cfg}
        if not isinstance(cfg, dict):
            cfg = {"enabled": False}
        d: Dict[str, Any] = {"enabled": bool(cfg.get("enabled", False))}
        if isinstance(cfg.get("input"), dict):
            d["input"] = cfg.get("input")
        if isinstance(cfg.get("override"), dict):
            d["override"] = cfg.get("override")
        devices_compact[device_type] = d
    return {
        "template_id": profile.get("template_id"),
        "profile_id": profile.get("profile_id"),
        "profile_label": profile.get("profile_label"),
        "scenario_hint": profile.get("scenario_hint"),
        "type": household.get("type"),
        "ownership": household.get("ownership"),
        "people_count": household.get("people_count"),
        "occupancy_pattern": household.get("occupancy_pattern"),
        "wfh_days": household.get("work_from_home_days"),
        "monthly_bill": energy.get("monthly_bill"),
        "monthly_usage_kwh": energy.get("monthly_usage_kwh"),
        "has_solar": bool(solar.get("has_solar")),
        "has_battery": bool(battery.get("has_battery")),
        "has_ev": bool(ev.get("has_ev") or ev.get("planning_ev")),
        "enabled_devices": enabled_devices,
        "devices": devices_compact,
    }


def profile_to_interval_input(profile: Dict[str, Any]) -> Dict[str, Any]:
    household = profile.get("household") or {}
    appliances = household.get("appliances") or {}
    life = household.get("lifestyle_signals") or {}
    ev = profile.get("ev") or {}
    solar = profile.get("solar") or {}

    devices: List[Dict[str, Any]] = []
    device_inputs = profile.get("device_inputs") if isinstance(profile.get("device_inputs"), dict) else {}
    if device_inputs:
        for device_type in DEVICE_TYPES_ALL:
            cfg = device_inputs.get(device_type, {"enabled": False})
            if isinstance(cfg, bool):
                cfg = {"enabled": cfg}
            if not isinstance(cfg, dict):
                cfg = {"enabled": False}
            entry: Dict[str, Any] = {
                "id": f"{device_type}_1",
                "type": device_type,
                "enabled": bool(cfg.get("enabled", False)),
            }
            if isinstance(cfg.get("input"), dict):
                entry["input"] = cfg.get("input")
            if isinstance(cfg.get("override"), dict):
                entry["override"] = cfg.get("override")
            devices.append(entry)
    else:
        # Backward fallback if profile lacks explicit device_inputs.
        if bool(life.get("has_air_conditioning")):
            devices.append({"id": "ac_1", "type": "air_conditioning", "enabled": True})
        if bool(life.get("has_electric_heating")):
            devices.append({"id": "heat_1", "type": "electric_heating", "enabled": True})
        if bool(life.get("has_pool")) or bool(appliances.get("pool_pump")):
            devices.append({"id": "pool_1", "type": "pool_pump", "enabled": True})
        if bool(appliances.get("dishwasher")):
            devices.append({"id": "dw_1", "type": "dishwasher", "enabled": True})
        if bool(appliances.get("dryer")):
            devices.append({"id": "dryer_1", "type": "dryer", "enabled": True})
        if bool(appliances.get("induction_cooktop")):
            devices.append({"id": "cook_1", "type": "induction_cooktop", "enabled": True})
        if bool(ev.get("has_ev") or ev.get("planning_ev")):
            charging = ev.get("charging") if isinstance(ev.get("charging"), dict) else {}
            time_of_day = "overnight"
            pattern = str(charging.get("pattern") or "").lower()
            if "day" in pattern:
                time_of_day = "daytime"
            elif "evening" in pattern:
                time_of_day = "evening"
            devices.append({"id": "ev_1", "type": "ev_charging", "enabled": True, "input": {"time_of_day": time_of_day}})
        if bool(life.get("has_electric_hot_water")):
            devices.append({"id": "hw_1", "type": "electric_hot_water_controlled_load", "enabled": True})

    out = {
        "date": date.today().isoformat(),
        "location": {"postcode": (profile.get("location") or {}).get("postcode")},
        "household": {"controlled_load_present": bool(household.get("controlled_load_present"))},
        "devices": devices,
        "solar": {
            "has_solar": bool(solar.get("has_solar")),
            "system": {"size_kw": (((solar.get("system") or {}).get("size_kw")) if isinstance(solar.get("system"), dict) else None)},
            "performance": (solar.get("performance") if isinstance(solar.get("performance"), dict) else {}),
        },
    }
    return out


def _compact_interval_input(interval_input: Dict[str, Any]) -> Dict[str, Any]:
    devices = interval_input.get("devices") if isinstance(interval_input.get("devices"), list) else []
    enabled = 0
    by_type: Dict[str, int] = {}
    for d in devices:
        if not isinstance(d, dict):
            continue
        if bool(d.get("enabled")):
            enabled += 1
        t = str(d.get("type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "date": interval_input.get("date"),
        "location": interval_input.get("location"),
        "household": interval_input.get("household"),
        "solar": interval_input.get("solar"),
        "device_count_total": len(devices),
        "device_count_enabled": enabled,
        "device_type_counts": by_type,
    }


def _compact_interval_result(interval_result: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for meter in ["E1", "E2", "B1"]:
        meter_obj = interval_result.get(meter) if isinstance(interval_result.get(meter), dict) else {}
        reads = meter_obj.get("interval_reads") if isinstance(meter_obj.get("interval_reads"), list) else []
        reads_24 = [float(v) for v in reads[:24]] if reads else []
        out[meter] = {
            "interval_reads": reads_24,
            "daily_kwh": round(sum(reads_24), 4),
        }
    solar_gen_obj = interval_result.get("solar_generation") if isinstance(interval_result.get("solar_generation"), dict) else {}
    solar_reads = solar_gen_obj.get("interval_reads") if isinstance(solar_gen_obj.get("interval_reads"), list) else []
    if solar_reads:
        solar_24 = [float(v) for v in solar_reads[:24]]
        out["solar_generation"] = {
            "interval_reads": solar_24,
            "daily_kwh": round(sum(solar_24), 4),
        }
    return out


def _month_year_from_profile(profile: Dict[str, Any]) -> (int, int):
    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    month_of_bill = energy.get("month_of_bill")
    if isinstance(month_of_bill, str) and len(month_of_bill) >= 7 and month_of_bill[4] == "-":
        try:
            y = int(month_of_bill[0:4])
            m = int(month_of_bill[5:7])
            if 1 <= m <= 12:
                return y, m
        except Exception:
            pass
    today = date.today()
    return today.year, today.month


def _year_from_profile(profile: Dict[str, Any]) -> int:
    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    month_of_bill = energy.get("month_of_bill")
    if isinstance(month_of_bill, str) and len(month_of_bill) >= 4:
        try:
            y = int(month_of_bill[0:4])
            if 1900 <= y <= 2100:
                return y
        except Exception:
            pass
    return date.today().year


def _holiday_map_for_month(postcode: str, year: int, month: int) -> Dict[date, str]:
    """
    Best-effort AU public holiday map for the month.
    Uses volta-guradian/interval_data to resolve state from postcode, then python-holidays.
    """
    try:
        import holidays as pyholidays  # type: ignore
    except Exception:
        return {}

    try:
        resolve_region = _load_function(INTERVAL_DATA_SYNTH_FILE, "resolve_region_profile")
        region = resolve_region(str(postcode or ""), country="AU")
        state = getattr(region, "state", None) or "VIC"
    except Exception:
        state = "VIC"

    # Build holidays for the full year, then filter to month.
    try:
        cal = pyholidays.country_holidays("AU", subdiv=str(state), years=[int(year)])
    except Exception:
        cal = pyholidays.country_holidays("AU", years=[int(year)])

    out: Dict[date, str] = {}
    for d, name in cal.items():
        try:
            if isinstance(d, date) and d.year == year and d.month == month:
                out[d] = str(name)
        except Exception:
            continue
    return out


def _holiday_map_for_year(postcode: str, year: int) -> Dict[date, str]:
    """
    Best-effort AU public holiday map for the whole year.
    Uses volta-guradian/interval_data to resolve state from postcode, then python-holidays.
    """
    try:
        import holidays as pyholidays  # type: ignore
    except Exception:
        return {}

    try:
        resolve_region = _load_function(INTERVAL_DATA_SYNTH_FILE, "resolve_region_profile")
        region = resolve_region(str(postcode or ""), country="AU")
        state = getattr(region, "state", None) or "VIC"
    except Exception:
        state = "VIC"

    try:
        cal = pyholidays.country_holidays("AU", subdiv=str(state), years=[int(year)])
    except Exception:
        cal = pyholidays.country_holidays("AU", years=[int(year)])

    out: Dict[date, str] = {}
    for d, name in cal.items():
        try:
            if isinstance(d, date) and d.year == year:
                out[d] = str(name)
        except Exception:
            continue
    return out


def _apply_hour_multipliers(shape_24: List[float], mult_24: List[float]) -> List[float]:
    if len(shape_24) != 24 or len(mult_24) != 24:
        return shape_24
    out = [max(0.0, float(shape_24[i]) * float(mult_24[i])) for i in range(24)]
    s = sum(out)
    if s <= 0:
        return shape_24
    # Renormalize to preserve total energy.
    factor = sum(shape_24) / s if sum(shape_24) > 0 else 1.0
    return [v * factor for v in out]


def _estimate_device_daily_kwh_from_inputs(device_type: str, cfg: Dict[str, Any]) -> Optional[float]:
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled")):
        return None
    inp = cfg.get("input")
    if isinstance(inp, dict) and inp.get("daily_kwh") is not None:
        try:
            return float(inp.get("daily_kwh"))
        except Exception:
            return None
    qty = None
    if isinstance(inp, dict) and inp.get("quantity") is not None:
        try:
            qty = float(inp.get("quantity"))
        except Exception:
            qty = None
    if qty is None:
        return None
    spec = DEVICE_QUANTITY_BASELINES.get(device_type)
    if not spec:
        return None
    base_daily = float(spec.get("default_daily_kwh", 0.0) or 0.0)
    base_qty = float(spec.get("default_quantity", 1.0) or 1.0)
    if base_daily <= 0 or base_qty <= 0:
        return None
    daily = (qty / base_qty) * base_daily

    # Align with interval-builder semantics for runs_per_week (dishwasher/dryer):
    # - quantity scaling first
    # - runs_per_week scales daily energy by runs_per_week/7 (when daily_kwh is not explicitly provided)
    if isinstance(inp, dict) and inp.get("runs_per_week") is not None and inp.get("daily_kwh") is None:
        try:
            runs_per_week = float(inp.get("runs_per_week"))
        except Exception:
            runs_per_week = None
        if runs_per_week is not None and runs_per_week >= 0:
            daily = daily * (runs_per_week / 7.0)

    return daily


def _apply_day_overrides_for_month_simulation(
    profile: Dict[str, Any],
    *,
    day_date: date,
    day_multiplier: float,
    is_weekend: bool,
    is_holiday: bool,
    is_wfh: bool,
) -> None:
    """
    Keep the same device set/shape from the selected profile, but adjust daily energy for the day.
    Uses per-device override.daily_kwh so we don't mutate quantities or accidentally disable devices.
    """
    device_inputs = profile.get("device_inputs")
    if not isinstance(device_inputs, dict):
        return
    household = profile.get("household") if isinstance(profile.get("household"), dict) else {}
    occ = household.get("occupancy_pattern")
    occ_tokens = {str(x).strip().lower() for x in occ} if isinstance(occ, list) else set()
    season = _season_from_month_au(day_date.month)

    # Coarse per-day occupancy mode derived from profile tokens + WFH calendar.
    if is_holiday and "public_holiday_away" in occ_tokens:
        day_occ_mode = "away"
    elif is_wfh or "weekday_daytime_home" in occ_tokens:
        day_occ_mode = "daytime"
    elif "weekday_evening_only" in occ_tokens and not is_weekend and not is_holiday:
        day_occ_mode = "evening"
    elif "school_term_pattern" in occ_tokens:
        day_occ_mode = "family"
    else:
        day_occ_mode = "mixed"

    # Don't scale always-on devices as aggressively.
    core_devices = {"refrigerator", "wifi_router"}

    for device_type, cfg in device_inputs.items():
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled")):
            continue
        # Ensure interval builder sees the correct season per simulated day/month.
        if isinstance(cfg.get("input"), dict) and "season" in cfg["input"]:
            cfg["input"]["season"] = season
        base_kwh = _estimate_device_daily_kwh_from_inputs(device_type, cfg)
        if base_kwh is None:
            continue
        mult = float(day_multiplier)
        if device_type in core_devices:
            mult = 1.0 + ((mult - 1.0) * 0.35)
        # Simple weekend/holiday bias: discretionary devices shift a bit up.
        if (is_weekend or is_holiday) and device_type in {"dishwasher", "dryer", "induction_cooktop"}:
            mult *= 1.04
        # Seasonal occupancy tokens (very lightweight).
        if season == "winter" and "winter_high_occupancy" in occ_tokens:
            mult *= 1.06
        if season == "summer" and "summer_low_occupancy" in occ_tokens:
            mult *= 0.92
        if is_holiday and day_occ_mode == "away":
            # If user says they tend to be away on public holidays, drop discretionary usage.
            if device_type not in core_devices:
                mult *= 0.75

        cfg.setdefault("override", {})
        if isinstance(cfg["override"], dict):
            # Shape-following rule: override windows for a few devices based on occupancy mode.
            # This makes weekday vs weekend vs holiday profiles visibly different without changing the device set.
            def set_windows(windows: List[Dict[str, Any]]):
                cfg["override"]["windows"] = windows
            cfg["override"]["daily_kwh"] = round(max(0.0, base_kwh * mult), 3)
            if device_type in {"dishwasher", "dryer"}:
                if day_occ_mode in {"daytime", "family"} or is_weekend or is_holiday:
                    set_windows([{"start_hour": 11, "end_hour": 15, "total_kwh": cfg["override"]["daily_kwh"]}])
                else:
                    set_windows([{"start_hour": 19, "end_hour": 22, "total_kwh": cfg["override"]["daily_kwh"]}])
            elif device_type == "induction_cooktop":
                if day_occ_mode in {"daytime", "family"} or is_weekend or is_holiday:
                    # Split lunch + dinner.
                    k = cfg["override"]["daily_kwh"]
                    set_windows(
                        [
                            {"start_hour": 12, "end_hour": 13, "total_kwh": round(k * 0.35, 3)},
                            {"start_hour": 18, "end_hour": 20, "total_kwh": round(k * 0.65, 3)},
                        ]
                    )
                else:
                    set_windows([{"start_hour": 17, "end_hour": 20, "total_kwh": cfg["override"]["daily_kwh"]}])
            elif device_type == "air_conditioning":
                if day_occ_mode in {"daytime", "family"} or is_weekend or is_holiday:
                    set_windows([{"start_hour": 12, "end_hour": 18, "total_kwh": cfg["override"]["daily_kwh"]}])
                else:
                    set_windows([{"start_hour": 18, "end_hour": 23, "total_kwh": cfg["override"]["daily_kwh"]}])
            elif device_type == "electric_heating":
                if day_occ_mode in {"daytime", "family"} or is_weekend or is_holiday:
                    k = cfg["override"]["daily_kwh"]
                    set_windows(
                        [
                            {"start_hour": 6, "end_hour": 9, "total_kwh": round(k * 0.40, 3)},
                            {"start_hour": 17, "end_hour": 22, "total_kwh": round(k * 0.60, 3)},
                        ]
                    )
                else:
                    set_windows([{"start_hour": 17, "end_hour": 22, "total_kwh": cfg["override"]["daily_kwh"]}])


def _build_month_interval_follow_selected(
    *,
    profile: Dict[str, Any],
    selected_compact_interval: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a month-long interval series by *following the selected profile's 24h interval shape*,
    then applying weekday/weekend/holiday day-level adjustments.

    This avoids generating a completely different interval profile (which can diverge from device_inputs).
    """
    y, m = _month_year_from_profile(profile)
    days_in_month = calendar.monthrange(y, m)[1]
    postcode = str((((profile.get("location") or {}).get("postcode")) or "")).strip()
    holidays_map = _holiday_map_for_month(postcode, y, m)

    def _reads_24(meter: str) -> List[float]:
        r = ((selected_compact_interval.get(meter) or {}).get("interval_reads")) if isinstance(selected_compact_interval.get(meter), dict) else []
        if not isinstance(r, list) or not r:
            return [0.0] * 24
        r24 = [float(v) for v in r[:24]]
        if len(r24) < 24:
            r24 = r24 + ([0.0] * (24 - len(r24)))
        return r24

    base_e1 = _reads_24("E1")
    base_e2 = _reads_24("E2")
    base_solar = []
    if isinstance(selected_compact_interval.get("solar_generation"), dict):
        base_solar = _reads_24("solar_generation")
    else:
        base_solar = [0.0] * 24

    # Weekend adjustment: slightly flatter evening peak, a bit more midday.
    weekend_hour_mult = [
        1.00, 1.00, 1.00, 1.00, 0.98, 0.98, 0.97, 0.97,
        1.03, 1.04, 1.05, 1.05, 1.04, 1.03, 1.02, 1.01,
        1.00, 0.98, 0.95, 0.95, 0.96, 0.98, 1.00, 1.00,
    ]

    month_reads: Dict[str, List[float]] = {"E1": [], "E2": [], "B1": [], "solar_generation": []}
    month_daily_kwh: Dict[str, List[float]] = {"E1": [], "E2": [], "B1": [], "solar_generation": []}
    weekend_days = 0
    holiday_days = 0

    # Decide the anchor monthly usage target (kWh). If not present, just use the base day * days.
    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    target_monthly_kwh = energy.get("monthly_usage_kwh")
    try:
        target_monthly_kwh_f = float(target_monthly_kwh) if target_monthly_kwh is not None else None
    except Exception:
        target_monthly_kwh_f = None
    base_daily_total = sum(base_e1) + sum(base_e2)
    if target_monthly_kwh_f is None:
        target_monthly_kwh_f = base_daily_total * float(days_in_month)

    # Precompute unscaled daily totals to normalize to the monthly target.
    daily_scalars: List[float] = []
    for day_num in range(1, days_in_month + 1):
        d = date(y, m, day_num)
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_map
        if is_weekend:
            weekend_days += 1
        if is_holiday:
            holiday_days += 1

        # Slightly higher energy on weekends/holidays (tunable).
        scale = 1.00
        if is_weekend:
            scale *= 1.05
        if is_holiday:
            scale *= 1.03
        daily_scalars.append(scale)

    # Normalize the scalars so the monthly sum matches the target.
    scalar_sum = sum(daily_scalars) if daily_scalars else 1.0
    norm = (float(target_monthly_kwh_f) / max(base_daily_total * scalar_sum, 1e-9)) if base_daily_total > 0 else 1.0

    for day_num in range(1, days_in_month + 1):
        d = date(y, m, day_num)
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_map
        scale = daily_scalars[day_num - 1] * norm

        e1 = [v * scale for v in base_e1]
        if is_weekend or is_holiday:
            e1 = _apply_hour_multipliers(e1, weekend_hour_mult)
        e2 = [v * scale for v in base_e2]

        # Solar stays as base daily shape (no weekday/weekend effects).
        solar = base_solar[:]

        # Export B1 computed as solar minus instantaneous consumption (E1+E2).
        b1 = [max(0.0, solar[h] - (e1[h] + e2[h])) for h in range(24)]

        month_reads["E1"].extend(e1)
        month_reads["E2"].extend(e2)
        month_reads["B1"].extend(b1)
        month_reads["solar_generation"].extend(solar)
        month_daily_kwh["E1"].append(round(sum(e1), 4))
        month_daily_kwh["E2"].append(round(sum(e2), 4))
        month_daily_kwh["B1"].append(round(sum(b1), 4))
        month_daily_kwh["solar_generation"].append(round(sum(solar), 4))

    return {
        "profile_type": "month_interval_follow_selected",
        "month": f"{y:04d}-{m:02d}",
        "days": days_in_month,
        "holiday_days_in_month": holiday_days,
        "weekend_days_in_month": weekend_days,
        "E1": {
            "interval_reads": month_reads["E1"],
            "daily_kwh": month_daily_kwh["E1"],
            "monthly_kwh": round(sum(month_daily_kwh["E1"]), 4),
        },
        "E2": {
            "interval_reads": month_reads["E2"],
            "daily_kwh": month_daily_kwh["E2"],
            "monthly_kwh": round(sum(month_daily_kwh["E2"]), 4),
        },
        "B1": {
            "interval_reads": month_reads["B1"],
            "daily_kwh": month_daily_kwh["B1"],
            "monthly_kwh": round(sum(month_daily_kwh["B1"]), 4),
        },
        "solar_generation": {
            "interval_reads": month_reads["solar_generation"],
            "daily_kwh": month_daily_kwh["solar_generation"],
            "monthly_kwh": round(sum(month_daily_kwh["solar_generation"]), 4),
        },
    }


def _build_wfh_dates_for_year(*, postcode: str, profile_id: Any, year: int, wfh_days: int) -> set[date]:
    """
    Deterministic WFH calendar for the full year.
    Picks N weekdays per ISO week.
    """
    wfh_days = max(0, min(5, int(wfh_days or 0)))
    if wfh_days <= 0:
        return set()

    seed_str = f"{postcode}|{profile_id}|{year:04d}|wfh"
    seed_int = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:16], 16)
    rng = Random(seed_int)

    by_week: Dict[int, List[date]] = {}
    for month in range(1, 13):
        days_in_month = calendar.monthrange(year, month)[1]
        for day_num in range(1, days_in_month + 1):
            d = date(year, month, day_num)
            if d.weekday() >= 5:
                continue
            iso = d.isocalendar()
            week_no = iso[1] if isinstance(iso, tuple) else getattr(iso, "week", None)
            if week_no is None:
                week_no = int((d.timetuple().tm_yday - 1) / 7) + 1
            by_week.setdefault(int(week_no), []).append(d)

    wfh_dates: set[date] = set()
    for _, ds in by_week.items():
        ds2 = ds[:]
        rng.shuffle(ds2)
        for d in ds2[: min(wfh_days, len(ds2))]:
            wfh_dates.add(d)
    return wfh_dates


def _simulate_month_from_flow(
    *,
    profile: Dict[str, Any],
    build_interval_fn,
    year: int,
    month: int,
    holidays_year_map: Dict[date, str],
    wfh_year_dates: set[date],
    target_monthly_kwh_override: Optional[float],
) -> Dict[str, Any]:
    days_in_month = calendar.monthrange(year, month)[1]

    # Day multipliers then normalized so the month stays anchored.
    raw_mult: List[float] = []
    weekend_days = 0
    holiday_days = 0
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_year_map
        if is_weekend:
            weekend_days += 1
        if is_holiday:
            holiday_days += 1
        mult = 1.0
        if is_weekend:
            mult *= 1.05
        if is_holiday:
            mult *= 1.03
        raw_mult.append(mult)

    norm = (float(days_in_month) / max(sum(raw_mult), 1e-9)) if raw_mult else 1.0
    daily_mult_base = [m * norm for m in raw_mult]

    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    target_monthly_kwh = target_monthly_kwh_override if target_monthly_kwh_override is not None else energy.get("monthly_usage_kwh")
    try:
        target_monthly_kwh_f = float(target_monthly_kwh) if target_monthly_kwh is not None else None
    except Exception:
        target_monthly_kwh_f = None

    def _run(daily_mult: List[float]) -> List[Dict[str, Any]]:
        out_days: List[Dict[str, Any]] = []
        meters = ["E1", "E2", "B1"]
        for day_num in range(1, days_in_month + 1):
            d = date(year, month, day_num)
            is_weekend = d.weekday() >= 5
            holiday_name = holidays_year_map.get(d)
            is_holiday = holiday_name is not None
            is_wfh = d in wfh_year_dates
            day_profile = deepcopy(profile)
            _apply_day_overrides_for_month_simulation(
                day_profile,
                day_date=d,
                day_multiplier=daily_mult[day_num - 1],
                is_weekend=is_weekend,
                is_holiday=is_holiday,
                is_wfh=is_wfh,
            )

            interval_input = profile_to_interval_input(day_profile)
            interval_input["date"] = d.isoformat()
            interval_result = build_interval_fn(interval_input)
            compact = _compact_interval_result(interval_result)

            day_payload: Dict[str, Any] = {
                "date": d.isoformat(),
                "is_weekend": is_weekend,
                "is_holiday": is_holiday,
                "holiday_name": holiday_name,
                "is_wfh": is_wfh,
            }

            for meter in meters:
                r = (compact.get(meter) or {}).get("interval_reads") or []
                r24 = [float(v) for v in r[:24]] if isinstance(r, list) else [0.0] * 24
                if len(r24) < 24:
                    r24 = r24 + ([0.0] * (24 - len(r24)))
                day_payload[meter] = {
                    "interval_reads": r24,
                    "daily_kwh": round(sum(r24), 4),
                }

            sg = (compact.get("solar_generation") or {}).get("interval_reads") or []
            sg24 = [float(v) for v in sg[:24]] if isinstance(sg, list) else [0.0] * 24
            if len(sg24) < 24:
                sg24 = sg24 + ([0.0] * (24 - len(sg24)))
            day_payload["solar_generation"] = {
                "interval_reads": sg24,
                "daily_kwh": round(sum(sg24), 4),
            }

            out_days.append(day_payload)
        return out_days

    correction_factor = 1.0
    days_final = _run(daily_mult_base)
    if target_monthly_kwh_f is not None:
        for _ in range(3):
            gross_monthly = float(sum(d["E1"]["daily_kwh"] for d in days_final) + sum(d["E2"]["daily_kwh"] for d in days_final))
            if gross_monthly <= 0:
                break
            correction_factor = float(target_monthly_kwh_f) / gross_monthly
            if abs(correction_factor - 1.0) <= 0.0005:
                break
            daily_mult_base = [m * correction_factor for m in daily_mult_base]
            days_final = _run(daily_mult_base)

    month_reads: Dict[str, List[float]] = {"E1": [], "E2": [], "B1": []}
    month_daily_kwh: Dict[str, List[float]] = {"E1": [], "E2": [], "B1": []}
    solar_reads_all: List[float] = []
    solar_daily_kwh: List[float] = []
    for d in days_final:
        for meter in ["E1", "E2", "B1"]:
            r24 = d[meter]["interval_reads"]
            month_reads[meter].extend(r24)
            month_daily_kwh[meter].append(d[meter]["daily_kwh"])
        solar_reads_all.extend(d["solar_generation"]["interval_reads"])
        solar_daily_kwh.append(d["solar_generation"]["daily_kwh"])

    return {
        "days_in_month": days_in_month,
        "weekend_days_in_month": weekend_days,
        "holiday_days_in_month": holiday_days,
        "days_detail": days_final,
        "month_reads": month_reads,
        "month_daily_kwh": month_daily_kwh,
        "solar_reads_all": solar_reads_all,
        "solar_daily_kwh": solar_daily_kwh,
        "reconciliation": {
            "target_monthly_usage_kwh": round(float(target_monthly_kwh_f), 4) if target_monthly_kwh_f is not None else None,
            "generated_monthly_usage_kwh": round(float(sum(month_daily_kwh["E1"]) + sum(month_daily_kwh["E2"])), 4),
            "correction_factor_applied": round(float(correction_factor), 6),
        },
    }


def _build_year_interval_from_flow(
    *,
    profile: Dict[str, Any],
    build_interval_fn,
    year_override: Optional[int],
) -> Dict[str, Any]:
    year = int(year_override) if year_override is not None else _year_from_profile(profile)
    postcode = str((((profile.get("location") or {}).get("postcode")) or "")).strip()
    holidays_year_map = _holiday_map_for_year(postcode, year)

    household = profile.get("household") if isinstance(profile.get("household"), dict) else {}
    try:
        wfh_days = int(household.get("work_from_home_days", 0) or 0)
    except Exception:
        wfh_days = 0
    wfh_year_dates = _build_wfh_dates_for_year(postcode=postcode, profile_id=profile.get("profile_id"), year=year, wfh_days=wfh_days)

    energy = profile.get("energy_usage") if isinstance(profile.get("energy_usage"), dict) else {}
    target_monthly_kwh = energy.get("monthly_usage_kwh")
    try:
        target_monthly_kwh_f = float(target_monthly_kwh) if target_monthly_kwh is not None else None
    except Exception:
        target_monthly_kwh_f = None

    months: Dict[str, Any] = {}
    for month in range(1, 13):
        days_in_month = calendar.monthrange(year, month)[1]
        target_override = None
        # Preserve a stable daily target across different month lengths.
        if target_monthly_kwh_f is not None:
            target_override = float(target_monthly_kwh_f) * (float(days_in_month) / 30.0)

        sim = _simulate_month_from_flow(
            profile=profile,
            build_interval_fn=build_interval_fn,
            year=year,
            month=month,
            holidays_year_map=holidays_year_map,
            wfh_year_dates=wfh_year_dates,
            target_monthly_kwh_override=target_override,
        )

        key = f"{year:04d}-{month:02d}"
        months[key] = {
            "month": key,
            "days": sim["days_in_month"],
            "holiday_days_in_month": sim["holiday_days_in_month"],
            "weekend_days_in_month": sim["weekend_days_in_month"],
            "days_detail": sim["days_detail"],
            "reconciliation": sim["reconciliation"],
        }

    return {
        "profile_type": "year_interval_from_flow",
        "year": f"{year:04d}",
        "months": months,
    }


def _build_month_interval_from_flow(
    *,
    profile: Dict[str, Any],
    build_interval_fn,
) -> Dict[str, Any]:
    """
    Month simulation that follows the current flow:
    - uses selected profile.device_inputs (E1/E2 device bucketing, plus solar + export B1 logic)
    - runs interval builder per day
    - applies weekday/weekend/holiday variability + normalizes to keep the month total stable
    """
    y, m = _month_year_from_profile(profile)
    days_in_month = calendar.monthrange(y, m)[1]
    postcode = str((((profile.get("location") or {}).get("postcode")) or "")).strip()

    holidays_year_map = _holiday_map_for_year(postcode, y)

    household = profile.get("household") if isinstance(profile.get("household"), dict) else {}
    try:
        wfh_days = int(household.get("work_from_home_days", 0) or 0)
    except Exception:
        wfh_days = 0
    wfh_year_dates = _build_wfh_dates_for_year(postcode=postcode, profile_id=profile.get("profile_id"), year=y, wfh_days=wfh_days)

    sim = _simulate_month_from_flow(
        profile=profile,
        build_interval_fn=build_interval_fn,
        year=y,
        month=m,
        holidays_year_map=holidays_year_map,
        wfh_year_dates=wfh_year_dates,
        target_monthly_kwh_override=None,
    )

    out = {
        "profile_type": "month_interval_from_flow",
        "month": f"{y:04d}-{m:02d}",
        "days": days_in_month,
        "holiday_days_in_month": sim["holiday_days_in_month"],
        "weekend_days_in_month": sim["weekend_days_in_month"],
        "E1": {
            "interval_reads": sim["month_reads"]["E1"],
            "daily_kwh": sim["month_daily_kwh"]["E1"],
            "monthly_kwh": round(sum(sim["month_daily_kwh"]["E1"]), 4),
        },
        "E2": {
            "interval_reads": sim["month_reads"]["E2"],
            "daily_kwh": sim["month_daily_kwh"]["E2"],
            "monthly_kwh": round(sum(sim["month_daily_kwh"]["E2"]), 4),
        },
        "B1": {
            "interval_reads": sim["month_reads"]["B1"],
            "daily_kwh": sim["month_daily_kwh"]["B1"],
            "monthly_kwh": round(sum(sim["month_daily_kwh"]["B1"]), 4),
        },
        "solar_generation": {
            "interval_reads": sim["solar_reads_all"],
            "daily_kwh": sim["solar_daily_kwh"],
            "monthly_kwh": round(sum(sim["solar_daily_kwh"]), 4),
        },
        "reconciliation": sim["reconciliation"],
    }
    return out


def _build_candidate_intervals(
    profiles: List[Dict[str, Any]],
    build_interval_fn,
) -> Dict[str, Any]:
    summaries: List[Dict[str, Any]] = []
    intervals: List[Dict[str, Any]] = []
    for i, profile in enumerate(profiles):
        interval_input = profile_to_interval_input(profile)
        interval_result = build_interval_fn(interval_input)
        compact_result = _compact_interval_result(interval_result)
        e1_daily = float((compact_result.get("E1") or {}).get("daily_kwh", 0.0))
        e2_daily = float((compact_result.get("E2") or {}).get("daily_kwh", 0.0))
        b1_daily = float((compact_result.get("B1") or {}).get("daily_kwh", 0.0))
        solar_daily = float((compact_result.get("solar_generation") or {}).get("daily_kwh", 0.0))
        est_monthly_bill = _estimate_monthly_bill_from_profile_interval(profile, compact_result)
        observed_monthly_bill = ((profile.get("energy_usage") or {}).get("monthly_bill"))
        if observed_monthly_bill is not None:
            try:
                observed_monthly_bill = float(observed_monthly_bill)
                bill_error_abs = round(abs(est_monthly_bill - observed_monthly_bill), 2)
                bill_error_pct = round((bill_error_abs / observed_monthly_bill) * 100.0, 2) if observed_monthly_bill > 0 else None
            except Exception:
                observed_monthly_bill = None
                bill_error_abs = None
                bill_error_pct = None
        else:
            bill_error_abs = None
            bill_error_pct = None
        summaries.append(
            {
                "index": i + 1,
                "profile_id": profile.get("profile_id"),
                "scenario_hint": profile.get("scenario_hint"),
                "E1_daily_kwh": round(e1_daily, 4),
                "E2_daily_kwh": round(e2_daily, 4),
                "B1_daily_kwh": round(b1_daily, 4),
                "solar_generation_daily_kwh": round(solar_daily, 4),
                "estimated_monthly_bill": est_monthly_bill,
                "observed_monthly_bill": observed_monthly_bill,
                "bill_alignment_error_abs": bill_error_abs,
                "bill_alignment_error_pct": bill_error_pct,
            }
        )
        intervals.append(
            {
                "index": i + 1,
                "profile_id": profile.get("profile_id"),
                "scenario_hint": profile.get("scenario_hint"),
                "interval": compact_result,
                "estimated_monthly_bill": est_monthly_bill,
                "observed_monthly_bill": observed_monthly_bill,
                "bill_alignment_error_abs": bill_error_abs,
                "bill_alignment_error_pct": bill_error_pct,
            }
        )
    return {"summaries": summaries, "intervals": intervals}


def _merge_profiles_with_intervals(
    summaries: List[Dict[str, Any]],
    candidate_intervals: Dict[str, Any],
) -> List[Dict[str, Any]]:
    interval_summaries = candidate_intervals.get("summaries") if isinstance(candidate_intervals.get("summaries"), list) else []
    intervals = candidate_intervals.get("intervals") if isinstance(candidate_intervals.get("intervals"), list) else []
    merged: List[Dict[str, Any]] = []
    for i, s in enumerate(summaries):
        out = deepcopy(s)
        daily = interval_summaries[i] if i < len(interval_summaries) and isinstance(interval_summaries[i], dict) else {}
        interval_item = intervals[i] if i < len(intervals) and isinstance(intervals[i], dict) else {}
        compact_interval = interval_item.get("interval") if isinstance(interval_item.get("interval"), dict) else {}
        out["interval_daily"] = {
            "E1_daily_kwh": daily.get("E1_daily_kwh"),
            "E2_daily_kwh": daily.get("E2_daily_kwh"),
            "B1_daily_kwh": daily.get("B1_daily_kwh"),
            "solar_generation_daily_kwh": daily.get("solar_generation_daily_kwh"),
        }
        out["billing_alignment"] = {
            "estimated_monthly_bill": daily.get("estimated_monthly_bill"),
            "observed_monthly_bill": daily.get("observed_monthly_bill"),
            "bill_alignment_error_abs": daily.get("bill_alignment_error_abs"),
            "bill_alignment_error_pct": daily.get("bill_alignment_error_pct"),
        }
        out["interval"] = compact_interval
        merged.append(out)
    return merged


def _auto_select_index(summaries: List[Dict[str, Any]], user_hints: Dict[str, Any]) -> int:
    """
    Choose a reasonable default profile when the user doesn't explicitly select.

    Priority:
    - If user provided a bill/usage anchor: pick closest-aligned candidate.
    - Otherwise: pick a "middle" plausible candidate (avoid extreme device mixes).
    """
    if not summaries:
        return 1

    energy_hints = user_hints.get("energy_usage") if isinstance(user_hints.get("energy_usage"), dict) else {}
    target_bill = energy_hints.get("monthly_bill")
    target_usage = energy_hints.get("monthly_usage_kwh")

    # Helper to compute implied daily consumption from interval summary.
    def daily_consumption(s: Dict[str, Any]) -> float:
        d = s.get("interval_daily") if isinstance(s.get("interval_daily"), dict) else {}
        e1 = float(d.get("E1_daily_kwh") or 0.0)
        e2 = float(d.get("E2_daily_kwh") or 0.0)
        b1 = float(d.get("B1_daily_kwh") or 0.0)
        solar = float(d.get("solar_generation_daily_kwh") or 0.0)
        # Total consumption ~= grid import + self-consumed solar.
        return e1 + e2 + max(0.0, solar - b1)

    # 1) Bill anchor: pick the candidate with smallest alignment error.
    if target_bill is not None:
        best = None
        for s in summaries:
            align = s.get("billing_alignment") if isinstance(s.get("billing_alignment"), dict) else {}
            err_abs = align.get("bill_alignment_error_abs")
            err_pct = align.get("bill_alignment_error_pct")
            try:
                key = (
                    float(err_pct) if err_pct is not None else 1e9,
                    float(err_abs) if err_abs is not None else 1e9,
                )
            except Exception:
                continue
            if best is None or key < best[0]:
                best = (key, int(s.get("index") or 1))
        if best is not None:
            return max(1, min(5, best[1]))

    # 2) Usage anchor: pick closest implied monthly consumption.
    if target_usage is not None:
        best = None
        for s in summaries:
            try:
                est_monthly = daily_consumption(s) * 30.0
                key = abs(est_monthly - float(target_usage))
            except Exception:
                continue
            if best is None or key < best[0]:
                best = (key, int(s.get("index") or 1))
        if best is not None:
            return max(1, min(5, best[1]))

    # 3) No anchors: avoid extremes by preferring median daily consumption + fewer heavy devices.
    daily_vals = [daily_consumption(s) for s in summaries]
    daily_sorted = sorted(daily_vals)
    median = daily_sorted[len(daily_sorted) // 2]

    heavy_devices = {"electric_heating", "pool_pump", "dryer", "induction_cooktop", "ev_charging"}
    best = None
    for s in summaries:
        devices = s.get("devices") if isinstance(s.get("devices"), dict) else {}
        heavy_enabled = 0
        for d in heavy_devices:
            cfg = devices.get(d)
            if isinstance(cfg, dict) and bool(cfg.get("enabled")):
                heavy_enabled += 1
        d = daily_consumption(s)
        # Penalize heavy devices more than being slightly above/below median usage.
        key = (heavy_enabled * 10.0) + abs(d - median)
        if best is None or key < best[0]:
            best = (key, int(s.get("index") or 1))
    if best is not None:
        return max(1, min(5, best[1]))
    return 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate 5 profile options from postcode+retailer, select one, run scenario + interval.")
    p.add_argument("--postcode", required=True)
    p.add_argument("--retailer", required=True)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--user-inputs", type=str, default=None, help="Optional user input hints JSON to align generated profiles.")
    p.add_argument("--monthly-bill", type=float, default=None, help="Optional monthly bill hint.")
    p.add_argument("--monthly-usage-kwh", type=float, default=None, help="Optional monthly usage hint (kWh).")
    p.add_argument("--month-of-bill", type=str, default=None, help="Optional bill month hint in YYYY-MM.")
    p.add_argument("--people-count", type=str, default=None, help="Optional people count hint (e.g., 1, 2, 3, 4+).")
    p.add_argument("--household-type", type=str, default=None, help="Optional dwelling type hint (Apartment/Townhouse/Detached).")
    p.add_argument("--ownership", type=str, default=None, help="Optional ownership hint (rent/own).")
    p.add_argument("--wfh-days", type=int, default=None, help="Optional work-from-home days hint (0-5).")
    p.add_argument("--occupancy-pattern", type=str, default=None, help="Optional comma-separated occupancy pattern hint.")
    p.add_argument("--has-solar", type=str, default=None, help="Optional solar hint: true/false.")
    p.add_argument("--has-battery", type=str, default=None, help="Optional battery hint: true/false.")
    p.add_argument("--has-ev", type=str, default=None, help="Optional EV hint: true/false.")
    p.add_argument("--select", type=int, default=None, help="Selected profile index (1-5). If omitted, auto-select.")
    p.add_argument("--list-only", action="store_true", help="Print generated profile summaries only.")
    p.add_argument(
        "--with-scenario",
        action="store_true",
        help="Include scenario_checker result (get_scenario) in output. Default: off.",
    )
    p.add_argument(
        "--with-month-interval",
        action="store_true",
        help="Include synthetic interval series for the whole month for the selected profile (via interval builder day simulation). Default: off.",
    )
    p.add_argument(
        "--with-year-interval",
        action="store_true",
        help="Include synthetic interval series for the whole year for the selected profile, displayed as months -> days (via interval builder day simulation). Default: off.",
    )
    p.add_argument(
        "--year-of-bill",
        type=int,
        default=None,
        help="Override the year used for --with-year-interval (defaults to energy_usage.month_of_bill year or current year).",
    )
    p.add_argument("--full-output", action="store_true", help="Include full selected profiles + full interval payload (non-compact).")
    p.add_argument("--out", type=str, default=None, help="Output JSON path.")
    p.add_argument("--pretty", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    seed_value = _seed_from_inputs(args.postcode, args.retailer, args.seed)
    rng = Random(seed_value)
    profiles = generate_candidate_profiles(args.postcode, args.retailer, rng)
    build_interval = _load_function(INTERVAL_BUILDER_FILE, "build_interval_read_day_24")
    user_hints = _load_user_hints(args)

    updated_profiles: List[Dict[str, Any]] = []
    for i, profile in enumerate(profiles):
        cur = deepcopy(profile)
        if user_hints:
            cur = _apply_user_hints_to_profile(cur, user_hints, Random(seed_value + 701 + (i * 17)))
            cur = _apply_hard_constraints(cur, user_hints, user_hints)
            cur = _reconcile_profile_consistency(cur)
            cur = _calibrate_profile_to_targets(cur, build_interval, user_hints)
        else:
            cur = _reconcile_profile_consistency(cur)
        _set_profile_identity(cur)
        updated_profiles.append(cur)
    profiles = updated_profiles

    summaries = [{"index": i + 1, **_summary(p)} for i, p in enumerate(profiles)]
    candidate_intervals = _build_candidate_intervals(profiles, build_interval)
    summaries = _merge_profiles_with_intervals(summaries, candidate_intervals)

    if args.list_only:
        print(
            json.dumps(
                {
                    "seed": seed_value,
                    "user_input_hints_applied": user_hints,
                    "profiles": summaries,
                },
                ensure_ascii=False,
                indent=2 if args.pretty else None,
            )
        )
        return

    if args.select is None:
        idx = _auto_select_index(summaries, user_hints)
    else:
        idx = max(1, min(5, int(args.select)))
    selected_before = deepcopy(profiles[idx - 1])
    selected_after = deepcopy(selected_before)

    selected_after = _reconcile_profile_consistency(selected_after)
    _set_profile_identity(selected_after)

    scenario_result = None
    if args.with_scenario:
        get_scenario = _load_function(SCENARIO_CHECKER_FILE, "get_scenario")
        scenario_result = get_scenario(selected_after)
        available_scenarios = (scenario_result or {}).get("available_scenario_names", [])
        if (
            isinstance(available_scenarios, list)
            and len(available_scenarios) > 0
            and selected_after.get("scenario_hint") not in available_scenarios
        ):
            selected_after["scenario_hint"] = available_scenarios[0]
            scenario_result = get_scenario(selected_after)
    interval_input = profile_to_interval_input(selected_after)
    interval_result = build_interval(interval_input)
    selected_compact_interval = _compact_interval_result(interval_result)
    selected_est_bill = _estimate_monthly_bill_from_profile_interval(selected_after, selected_compact_interval)
    selected_observed_bill = ((selected_after.get("energy_usage") or {}).get("monthly_bill"))
    if selected_observed_bill is not None:
        try:
            selected_observed_bill = float(selected_observed_bill)
            selected_bill_error_abs = round(abs(selected_est_bill - selected_observed_bill), 2)
            selected_bill_error_pct = round((selected_bill_error_abs / selected_observed_bill) * 100.0, 2) if selected_observed_bill > 0 else None
        except Exception:
            selected_observed_bill = None
            selected_bill_error_abs = None
            selected_bill_error_pct = None
    else:
        selected_bill_error_abs = None
        selected_bill_error_pct = None
    selected_summary = _summary(selected_after)
    selected_summary["billing_alignment"] = {
        "estimated_monthly_bill": selected_est_bill,
        "observed_monthly_bill": selected_observed_bill,
        "bill_alignment_error_abs": selected_bill_error_abs,
        "bill_alignment_error_pct": selected_bill_error_pct,
    }

    if args.full_output:
        output = {
            "input_required": {"postcode": args.postcode, "retailer": args.retailer},
            "seed": seed_value,
            "user_input_hints_applied": user_hints,
            "generated_profile_summaries": summaries,
            "selected_index": idx,
            "selected_profile": selected_after,
            "selected_profile_summary": selected_summary,
        }
        if args.with_scenario:
            output["scenario_result"] = scenario_result
        if args.with_month_interval or args.with_year_interval:
            interval_data: Dict[str, Any] = {}
            if args.with_month_interval:
                interval_data["month_interval"] = _build_month_interval_from_flow(
                    profile=selected_after,
                    build_interval_fn=build_interval,
                )
            if args.with_year_interval:
                interval_data["year_interval"] = _build_year_interval_from_flow(
                    profile=selected_after,
                    build_interval_fn=build_interval,
                    year_override=args.year_of_bill,
                )
            output["selected_profile"]["interval_data"] = interval_data
    else:
        output = {
            "input_required": {"postcode": args.postcode, "retailer": args.retailer},
            "seed": seed_value,
            "user_input_hints_applied": user_hints,
            "generated_profile_summaries": summaries,
            "selected_index": idx,
            "selected": {
                "index": idx,
                "summary": selected_summary,
            },
        }
        if args.with_scenario:
            output["scenario_result"] = scenario_result
        if args.with_month_interval or args.with_year_interval:
            interval_data2: Dict[str, Any] = {}
            if args.with_month_interval:
                interval_data2["month_interval"] = _build_month_interval_from_flow(
                    profile=selected_after,
                    build_interval_fn=build_interval,
                )
            if args.with_year_interval:
                interval_data2["year_interval"] = _build_year_interval_from_flow(
                    profile=selected_after,
                    build_interval_fn=build_interval,
                    year_override=args.year_of_bill,
                )
            output["selected"]["interval_data"] = interval_data2

    out_path: Path
    if args.out:
        out_path = Path(args.out)
    else:
        out_dir = Path(__file__).resolve().parent / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"profile_flow_{args.postcode}_{idx}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.pretty:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        base = {
            "out": str(out_path),
            "selected_index": idx,
        }
        if args.with_scenario:
            base["available_scenarios"] = (scenario_result or {}).get("available_scenario_names", [])
        print(
            json.dumps(
                base,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
