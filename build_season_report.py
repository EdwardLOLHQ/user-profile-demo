#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{float(v):.3f}"


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _render_markdown(summary: Dict[str, Any]) -> str:
    meta = summary.get("input", {})
    rows: List[Dict[str, Any]] = summary.get("profiles", [])
    generated_at = summary.get("generated_at", "-")

    lines: List[str] = []
    lines.append("# Season Comparison Report")
    lines.append("")
    lines.append(f"- Generated at: `{generated_at}`")
    lines.append(f"- Postcode: `{meta.get('postcode', '-')}`")
    lines.append(f"- Retailer: `{meta.get('retailer', '-')}`")
    lines.append(f"- Seed: `{meta.get('seed', '-')}`")
    lines.append(f"- Profiles tested: `{len(rows)}`")
    lines.append("")
    lines.append("## Profile Table")
    lines.append("")
    lines.append(
        "| # | Profile | People | WFH | Lighting S/W/Δ | AC S/W/Δ | Heating S/W/Δ | E1 S/W/Δ | E2 S/W/Δ | B1 S/W/Δ |"
    )
    lines.append("|---:|---|---:|---:|---|---|---|---|---|---|")

    for row in rows:
        summer = row.get("summer", {})
        winter = row.get("winter", {})
        delta = row.get("delta", {})
        lines.append(
            "| "
            f"{row.get('profile_index')} | "
            f"{row.get('profile_id')} | "
            f"{row.get('people_count')} | "
            f"{row.get('wfh_days')} | "
            f"{_fmt(summer.get('lighting_daily_kwh'))}/{_fmt(winter.get('lighting_daily_kwh'))}/{_fmt(delta.get('lighting_winter_minus_summer'))} | "
            f"{_fmt(summer.get('ac_daily_kwh'))}/{_fmt(winter.get('ac_daily_kwh'))}/{_fmt(delta.get('ac_winter_minus_summer'))} | "
            f"{_fmt(summer.get('heating_daily_kwh'))}/{_fmt(winter.get('heating_daily_kwh'))}/{_fmt(delta.get('heating_winter_minus_summer'))} | "
            f"{_fmt(summer.get('E1_daily_kwh'))}/{_fmt(winter.get('E1_daily_kwh'))}/{_fmt(delta.get('E1_winter_minus_summer'))} | "
            f"{_fmt(summer.get('E2_daily_kwh'))}/{_fmt(winter.get('E2_daily_kwh'))}/{_fmt(delta.get('E2_winter_minus_summer'))} | "
            f"{_fmt(summer.get('B1_daily_kwh'))}/{_fmt(winter.get('B1_daily_kwh'))}/{_fmt(delta.get('B1_winter_minus_summer'))} |"
        )

    lighting_up = sum(1 for r in rows if (r.get("delta", {}).get("lighting_winter_minus_summer") or 0) > 0)
    ac_down = sum(1 for r in rows if (r.get("delta", {}).get("ac_winter_minus_summer") or 0) < 0)
    heating_up = sum(1 for r in rows if (r.get("delta", {}).get("heating_winter_minus_summer") or 0) > 0)
    solar_profiles = [r for r in rows if "SOLAR" in str(r.get("scenario_hint", "")).upper()]
    b1_changed = sum(1 for r in solar_profiles if abs(float(r.get("delta", {}).get("B1_winter_minus_summer") or 0.0)) > 1e-9)

    lines.append("")
    lines.append("## Key Findings")
    lines.append("")
    lines.append(f"- Lighting increases in winter for `{lighting_up}/{len(rows)}` profiles.")
    lines.append(f"- Air-conditioning decreases in winter for `{ac_down}/{len(rows)}` profiles.")
    lines.append(f"- Electric heating increases in winter for `{heating_up}` profiles where heating exists.")
    lines.append(f"- For solar scenarios, B1 export changes in `{b1_changed}/{len(solar_profiles)}` profiles.")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- `S/W/Δ` means Summer value / Winter value / (Winter - Summer).")
    lines.append("- Summer month is fixed to `2026-01`, winter month is fixed to `2026-07` in this batch.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build markdown season comparison report from batch summary JSON.")
    parser.add_argument(
        "--summary",
        default="scenario-profile-flow-demo/outputs/season_batch/season_compare_summary.json",
        help="Path to season_compare_summary.json",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output markdown path (default: same folder as summary, file season_compare_report.md)",
    )
    args = parser.parse_args()

    summary_path = Path(args.summary).resolve()
    if not summary_path.exists():
        raise SystemExit(f"Summary file not found: {summary_path}")

    out_path = Path(args.out).resolve() if args.out else summary_path.parent / "season_compare_report.md"
    summary = _load(summary_path)
    report = _render_markdown(summary)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
