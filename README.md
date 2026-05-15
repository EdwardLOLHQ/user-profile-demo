# Scenario Profile Flow Demo

This demo implements the flow:

1. User enters required fields: `postcode` and `retailer`.
2. User can optionally provide input hints (monthly bill/usage, people count, ownership, type, WFH, occupancy, solar/battery/EV).
3. System generates 5 candidate user profiles (archetype-based + randomized values), then maps the hints into those profiles.
   - Hard constraints (e.g. `has_solar=false`, `has_ev=false`) are enforced.
   - Monthly bill/usage hints are used as calibration targets (profile-level alignment).
   - In anchored mode (when hints are provided), EV/Battery are not inferred unless explicitly set.
   - Each profile carries 3 identity fields:
     - `template_id`: original template/archetype id
     - `profile_id`: technical id (`prof_001`..`prof_005`), does not encode business logic
     - `profile_label`: human-readable semantic label (scenario/dwelling/people/occupancy/usage + optional suffixes)
4. User selects one profile.
5. The selected profile is sent to:
   - `scenario_checker` (`get_scenario`) (optional, `--with-scenario`)
   - interval builder (`build_interval_read_day_24`, bundled in this repo)
6. A single output JSON is produced for tracking.

Each generated profile also includes `device_inputs` with a full device list
(all handled devices are present; non-used ones are set to `enabled=false`).
Typical always-on devices (`lighting`, `refrigerator`, `wifi_router`) are enabled by default.
Each device now includes `input.quantity` so users can edit by count (e.g., number of lights / fridges / AC units).
The interval builder infers daily usage from quantity when `input.daily_kwh` is not provided.
Each enabled device also has default usage window fields:
- `input.start_hour`
- `input.end_hour`
Additional size-aware tuning is applied for:
- `lighting` (`quantity`)
- `refrigerator` (`quantity`)
- `wifi_router` (`quantity`)
- `dishwasher` (`runs_per_week`)
- `dryer` (`runs_per_week`)
- `induction_cooktop` (`quantity`)
- `electric_hot_water_controlled_load` (`quantity`)
- `air_conditioning` (`quantity`)
- `electric_heating` (`quantity`)

Season-aware adjustment (Australia seasons) is applied to:
- `lighting` (higher in winter, lower in summer)
- `air_conditioning` (higher in summer, lower in winter)
- `electric_heating` (higher in winter, lower in summer)

Season source:
- `energy_usage.month_of_bill` (if present, format `YYYY-MM`)
- fallback to current month

When `--edits` updates household/energy fields (for example `month_of_bill`, people count, WFH days),
`device_inputs` defaults are auto-recomputed unless you explicitly provide `device_inputs` in the edit patch.

## Run

From repo root:

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --pretty
```

If `--select` is omitted, the demo auto-selects a profile:
- if you provided a bill/usage anchor: it picks the closest-aligned candidate
- otherwise: it avoids extreme device mixes and picks a "middle" plausible candidate

With user-input hints (example):

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --monthly-bill 210 \
  --people-count 3 \
  --ownership own \
  --household-type Townhouse \
  --wfh-days 2 \
  --has-solar true \
  --pretty
```

Include scenario_checker output:

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --with-scenario \
  --pretty
```

Include simulated month interval output (days*24) for the selected profile:

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --month-of-bill 2026-07 \
  --with-month-interval \
  --pretty
```

Include simulated year interval output (months -> days -> 24 reads) for the selected profile:

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --year-of-bill 2026 \
  --with-year-interval \
  --pretty
```

Or pass a JSON file:

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --user-inputs scenario-profile-flow-demo/samples/user_inputs_example.json \
  --pretty
```

No-solar + bill anchor example:

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --user-inputs scenario-profile-flow-demo/samples/user_inputs_no_solar_bill_160.json \
  --pretty
```

List-only mode (see profile summaries first):

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --list-only
```

## Output

The output JSON contains:
- `user_input_hints_applied`
- generated profile summaries
- each summary now includes full `devices` (enabled + optional input/override)
- each summary now also includes:
  - `interval_daily` (daily E1/E2/B1/solar)
  - `billing_alignment` (`estimated_monthly_bill`, observed bill, and alignment error)
  - `interval` (compact 24-point interval arrays)
- compact `selected` block (`summary`)
- `scenario_checker` result (only when `--with-scenario`)
- `selected.interval_data.month_interval` (only when `--with-month-interval`, generated via interval builder day simulation)
- `selected.interval_data.year_interval` (only when `--with-year-interval`, generated via interval builder day simulation; displayed as months -> days)

Full payload mode (backward-compatible, includes full selected profiles and full interval payload):

```bash
python3 scenario-profile-flow-demo/flow_demo.py \
  --postcode 3000 \
  --retailer Origin \
  --select 4 \
  --full-output \
  --pretty
```

Season batch reporting:

```bash
python3 scenario-profile-flow-demo/build_season_report.py \
  --summary scenario-profile-flow-demo/outputs/season_batch/season_compare_summary.json
```
