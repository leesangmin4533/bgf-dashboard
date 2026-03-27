# FoodWasteRateCalibrator Status Analysis

- Analysis date: 2026-03-03
- Scope: Calibrator stall (mid=003/004), schema divergence (46704 vs 46513), waste_rate calculation discrepancy
- Key files analyzed:
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\prediction\food_waste_calibrator.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\infrastructure\database\schema.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\db\models.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\scheduler\daily_job.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\settings\constants.py`
  - `C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto\src\prediction\categories\food.py`

---

## Finding 1: Why Calibrator Stops Adjusting for mid=003 (+6.9%p) and mid=004 (+8.8%p)

### Root Cause: Compound Floor + Parameter Lower Bounds

The calibrator has **three** gates that can block adjustment even when error exceeds the deadband:

#### Gate A: Parameter Lower Bounds (at_limit)

For mid=003 (kimgab, expiry fallback=1 day -> expiry_group=`ultra_short`):

| Parameter | Lower Bound | Upper Bound |
|-----------|-------------|-------------|
| safety_days | **0.35** | 0.80 |
| gap_coefficient | **0.20** | 0.70 |

For mid=004 (sandwich, expiry fallback=2 days -> expiry_group=`short`):

| Parameter | Lower Bound | Upper Bound |
|-----------|-------------|-------------|
| safety_days | **0.45** | 1.00 |
| gap_coefficient | **0.30** | 0.80 |

When both parameters have already been decremented to their lower bounds, the `_reduce_order()` method returns `(None, None, None)` and the result reason becomes `"at_limit"`. The calibrator logs this at DEBUG level only, so it can appear silent.

**For mid=003 (ultra_short):** If safety_days has reached 0.35 and gap_coefficient has reached 0.20, no further reduction is possible. The compound floor (0.35 * 0.20 = 0.07) is below COMPOUND_FLOOR (0.15), so the compound floor gate would also trigger first.

**For mid=004 (short):** If safety_days has reached 0.45 and gap_coefficient has reached 0.30, compound = 0.45 * 0.30 = 0.135, which is below COMPOUND_FLOOR (0.15). The compound floor blocks before individual limits are even checked.

#### Gate B: Compound Floor (COMPOUND_FLOOR = 0.15)

```python
# food_waste_calibrator.py line 688-714
COMPOUND_FLOOR = 0.15

def _reduce_order(self, params, expiry_group, error):
    compound = params.safety_days * params.gap_coefficient
    if compound <= self.COMPOUND_FLOOR:
        return None, None, None  # "at_limit"
```

This is checked **before** any individual parameter adjustment. The log message says "compound floor" but the CalibrationResult reason is just `"at_limit"` (same as individual limit).

**Critical scenario for mid=003:**
- Default: safety_days=0.70, gap_coefficient=0.40 -> compound=0.28
- After several days of error > 0: safety_days decrements by 0.05 or 0.02 per day
- After ~7 adjustments: safety_days=0.35, gap=0.40 -> compound=0.14 < 0.15 -> BLOCKED
- gap_coefficient never gets a chance to reduce because compound floor triggers first

**Critical scenario for mid=004:**
- Default: safety_days=0.80, gap_coefficient=0.60 -> compound=0.48
- After adjustment: when safety_days reaches ~0.50, gap still 0.60 -> compound=0.30 (ok)
- Continued: safety_days=0.45 (short lower bound), gap=0.60 -> compound=0.27 (ok)
- Then gap starts reducing: gap=0.30 -> compound=0.45*0.30=0.135 < 0.15 -> BLOCKED

#### Gate C: Hysteresis (_check_consistent_direction)

This gate only applies when `error > 0` (waste rate above target). It requires the **previous day's** calibration record to also have `error > 0`. For sustained over-waste scenarios (like +6.9% and +8.8%), this should pass after the first day. However:

- The query uses `ORDER BY calibration_date DESC LIMIT 1`
- If the calibrator ran yesterday and recorded error > 0, today's error > 0 will pass
- If yesterday's record was error=0 (within deadband) or the clamp operation wrote error=0, then hysteresis blocks

**Specific clamp issue:** The `_clamp_stale_params()` method writes records with `error=0` (line 1057: `VALUES (?, ?, '', ?, 0, 0, 0, 0, ?, ?)`). If this clamp runs on the same day, it becomes the "most recent" record for that mid_cd, and the next hysteresis check sees `prev_error=0`, which does NOT match the `current_error > 0` direction check. This blocks adjustment for one day.

### Summary: Three Blocking Mechanisms

```
Error +6.9%p (mid=003) / +8.8%p (mid=004)

Step 1: Is error within deadband (+-2%p)?  -> NO (passes)
Step 2: Hysteresis: prev_error same sign?   -> Usually YES, but clamp can reset to 0
Step 3: Compound floor check                -> BLOCKS if safety*gap <= 0.15
Step 4: Individual parameter lower bound    -> BLOCKS if already at minimum
```

**Most likely cause:** Both mid=003 and mid=004 have hit the **compound floor** (0.15). The calibrator cannot reduce parameters further, so it reports `at_limit` and stops adjusting, even though the error is large.

### Recommended Fix

1. **Decouple the compound floor from individual adjustments.** Currently the floor prevents ANY reduction. Instead, allow gap_coefficient to reduce if safety_days is not at its own minimum (and vice versa), only blocking when BOTH are at their individual minimums.

2. **Expand the parameter surface.** The calibrator only adjusts safety_days and gap_coefficient. For persistent high waste rates where both are floored, introduce adjustment of `waste_buffer` (currently not used in `_reduce_order` despite being in the config range 1-5). Reducing waste_buffer would reduce the daily cap, providing another lever.

3. **Lower the compound floor.** COMPOUND_FLOOR=0.15 is conservative. For ultra_short items (1-day expiry), the theoretical minimum compound is 0.35*0.20=0.07. A floor of 0.10 would allow more room while still preventing zero-order states.

---

## Finding 2: Why Store 46704 is Stuck at Schema v30 While 46513 is at v34

### Root Cause: Migration System Does Not Apply to Store DBs

There are **two independent DB initialization paths**, and only the legacy path runs migrations:

#### Path 1: Legacy Migration (bgf_sales.db) -- HAS version tracking

```python
# run_scheduler.py line 1031:
from db.models import init_db
init_db()  # Applies SCHEMA_MIGRATIONS v1..v50 to bgf_sales.db
```

- `init_db()` in `src/db/models.py` opens `data/bgf_sales.db`
- Checks `schema_version` table for current version
- Applies missing migrations from `SCHEMA_MIGRATIONS` dict (v1 through v50)
- Records each version in `schema_version` table
- `food_waste_calibration` table is created by migration v32
- `small_cd` column is added by migration v48

#### Path 2: Store DB Initialization (stores/{id}.db) -- NO version tracking, NO migrations

```python
# daily_job.py line 196-197:
from src.infrastructure.database.schema import init_store_db
init_store_db(self.store_id)  # CREATE TABLE IF NOT EXISTS only
```

- `init_store_db()` in `src/infrastructure/database/schema.py` opens `data/stores/{store_id}.db`
- Executes `STORE_SCHEMA` list (CREATE TABLE IF NOT EXISTS for ~25 tables)
- Executes `STORE_INDEXES` list
- **No `schema_version` table**, no migration logic
- **`food_waste_calibration` is NOT in `STORE_SCHEMA`** -- it was never added there

#### Path 3: Legacy schema.py Migration (bgf_sales.db)

```python
# schema.py line 855-905: init_db() (duplicate of models.py init_db)
```

This is a copy of the models.py migration logic, also targeting bgf_sales.db only.

### Why 46704 shows v30 and 46513 shows v34

The version numbers "v30" and "v34" likely refer to the `schema_version` entries in the **legacy bgf_sales.db**, not in the store DBs themselves. Since `init_db()` runs at startup (line 1031 in run_scheduler.py) and applies to bgf_sales.db only, the "v30 vs v34" discrepancy could mean:

1. **Store 46704 was last active when bgf_sales.db was at v30**, and has not been run since (its legacy DB copy is stale)
2. **Or:** If each store has its own `schema_version` somehow, it means the store DB has never had migrations applied -- it only has whatever tables `init_store_db()` creates

### Critical Impact

Since `food_waste_calibration` is **not in STORE_SCHEMA** (schema.py), the table only exists in the store DBs if:
- It was manually created, OR
- The store DB was copied/inherited from bgf_sales.db during the DB split migration

For store 46704, if the table does not exist, the calibrator's `_get_conn()` returns a connection to the store DB, and every SQL query on `food_waste_calibration` fails with `OperationalError: no such table`, which is silently caught:

```python
# food_waste_calibrator.py line 175-177:
except sqlite3.OperationalError:
    # 테이블 미존재 (마이그레이션 전)
    return None
```

This means the calibrator **silently does nothing** for stores whose DBs lack the table.

### Recommended Fix

1. **Add `food_waste_calibration` to `STORE_SCHEMA` in `schema.py`** (with the small_cd column from v48 already included). This ensures `init_store_db()` creates the table.

2. **Also add `waste_slips`, `waste_slip_items`, and `waste_verification_log`** to `STORE_SCHEMA` -- these are also migration-only tables (v33, v34) that the calibrator's `_get_waste_stats()` queries.

3. **Consider adding a lightweight migration system to `init_store_db()`** that tracks a version and can apply ALTER TABLE statements. Without this, any future column additions to store-only tables require manual intervention.

---

## Finding 3: Why Calibrator's waste_rate Differs from Simple waste/order Ratio

### The Two Calculations

**Simple calculation (7-day daily_sales snapshot):**
```
mid=001, store=46513: waste=9, order=73, rate=12.3%
Period: last 7 days of daily_sales
Formula: disuse_qty / (sale_qty + disuse_qty)
```

**Calibrator calculation:**
```
mid=001, store=46513: actual=23.3%, ord=116, waste=27, sample_days=22
Period: last 21 days (FOOD_WASTE_CAL_LOOKBACK_DAYS)
```

### Discrepancy Explained: Four Sources of Difference

#### Source 1: Different Lookback Periods (7 days vs 21 days)

The simple calculation uses 7 days of daily_sales. The calibrator uses `FOOD_WASTE_CAL_LOOKBACK_DAYS = 21` days. With 21 days of data, the calibrator captures more waste events, including older high-waste periods that the 7-day window misses.

This is the **primary** source of the discrepancy. If waste was higher 8-21 days ago than in the last 7 days, the calibrator's 21-day rate will be higher.

#### Source 2: waste_slip_items Priority (lines 818-849)

The calibrator's `_get_waste_stats()` has a two-source priority system:

```python
# 1st: Try waste_slip_items (slip-based, more accurate)
waste_from_slip = SUM(wsi.qty) from waste_slip_items

# 2nd: Fallback to daily_sales.disuse_qty
total_waste_ds = SUM(disuse_qty) from daily_sales

# Decision:
if waste_from_slip > 0:
    total_waste = waste_from_slip  # Prefer slip data
else:
    total_waste = total_waste_ds   # Fallback
```

If `waste_slip_items` exists and has data, the waste quantity may differ from `daily_sales.disuse_qty` because:
- Slip data is collected from official waste documents (STGJ020_M0)
- daily_sales disuse_qty comes from the sales status page
- Timing differences in data collection can cause mismatches
- Slip data may include waste events not yet reflected in daily_sales

#### Source 3: total_order Definition

The calibrator defines `total_order = total_sold + total_waste` (line 889), which is `SUM(sale_qty) + SUM(disuse_qty)`. This is **not** the actual order quantity -- it's the total "throughput" (items that were either sold or wasted). This is mathematically identical to `waste / (sold + waste)` for the rate calculation.

The simple calculation likely uses the same formula, so this is not a source of difference by itself.

#### Source 4: sample_days Reporting

The calibrator reports `sample_days = COUNT(DISTINCT sales_date)` = 22 out of a 21-day window. This means there are 22 distinct dates with sales data (the window is `>= date('now', '-21 days')` which includes today, giving a 22-day range). This confirms the window is working as designed.

### Numerical Reconciliation

Given the calibrator data: ord=116, waste=27, rate=23.3%:
- 27 / 116 = 0.2328 (23.3%) -- consistent
- 116 = sold (89) + waste (27)

Given the simple 7-day data: waste=9, order=73, rate=12.3%:
- 9 / 73 = 0.1233 (12.3%) -- consistent
- The remaining 14 days contributed: waste=18, order=43 -> 18/43 = 41.9% waste rate

This means the **older 14 days had a significantly higher waste rate** (41.9%) than the recent 7 days (12.3%). The calibrator's 21-day window captures this older high-waste period, producing a blended rate of 23.3%.

### Implication

The calibrator is working as designed -- the 21-day window provides a more stable signal for parameter adjustment, smoothing out week-to-week variation. However, this means the calibrator reacts slowly to recent improvements (like the drop from 41.9% to 12.3% in the last 7 days).

### Recommendation

No code fix needed -- this is by design. However:
- Consider adding a **recency-weighted** waste rate calculation (e.g., exponential weighting favoring recent days) to make the calibrator more responsive to improving trends
- Consider logging both the 7-day and 21-day rates for operator visibility

---

## Summary of Issues and Severity

### Critical (Immediate Fix Required)

| # | Issue | Impact | File | Fix |
|---|-------|--------|------|-----|
| 1 | `food_waste_calibration` table missing from `STORE_SCHEMA` | Calibrator silently no-ops for stores without legacy migration | `schema.py` | Add table to `STORE_SCHEMA` list |
| 2 | `waste_slips`, `waste_slip_items` missing from `STORE_SCHEMA` | `_get_waste_stats()` always falls back to daily_sales | `schema.py` | Add tables to `STORE_SCHEMA` list |
| 3 | Store DB has no migration system | New columns (like small_cd from v48) never applied to store DBs | `schema.py` | Add lightweight migration to `init_store_db()` |

### Warning (Improvement Recommended)

| # | Issue | Impact | File | Fix |
|---|-------|--------|------|-----|
| 4 | Compound floor (0.15) blocks all reduction prematurely | mid=003/004 stuck with +6.9%/+8.8% error | `food_waste_calibrator.py` | Lower to 0.10 or decouple gates |
| 5 | `_clamp_stale_params()` writes error=0 records | Breaks hysteresis for next calibration cycle | `food_waste_calibrator.py` | Write NULL or skip hysteresis after clamp |
| 6 | `_reduce_order` does not use waste_buffer | Only 2 of 3 parameters are adjusted | `food_waste_calibrator.py` | Add waste_buffer as 3rd reduction lever |
| 7 | 21-day lookback slow to react to improving trends | Calibrator keeps reducing even after waste drops | `food_waste_calibrator.py` | Add recency weighting or dual-window |

### Info (Reference)

- The calibrator's waste_rate is mathematically correct for its 21-day window
- Hysteresis (error > 0 direction consistency) is bypassed for error < 0 (under-waste / stockout risk) -- this is intentional
- The `_increase_order` path has a 2.0x acceleration for severe under-order (>10%p) -- this asymmetry is appropriate
- The calibrator saves results even when not adjusting (for audit trail), which is good practice

---

## Appendix: Parameter Default Values and Bounds

### mid=003 (Kimgab, ultra_short)

| Parameter | Default | Cal Lower | Cal Upper | Compound Floor |
|-----------|---------|-----------|-----------|----------------|
| safety_days | 0.70 | 0.35 | 0.80 | |
| gap_coefficient | 0.40 | 0.20 | 0.70 | |
| compound (s*g) | 0.28 | 0.07 | 0.56 | **0.15** |

When safety_days reaches 0.375 and gap=0.40: compound=0.15 -> floor hit.
Remaining reduction headroom blocked: safety could go to 0.35, gap could go to 0.20, but compound floor prevents it.

### mid=004 (Sandwich, short)

| Parameter | Default | Cal Lower | Cal Upper | Compound Floor |
|-----------|---------|-----------|-----------|----------------|
| safety_days | 0.80 | 0.45 | 1.00 | |
| gap_coefficient | 0.60 | 0.30 | 0.80 | |
| compound (s*g) | 0.48 | 0.135 | 0.80 | **0.15** |

When safety_days=0.45 and gap=0.333: compound=0.15 -> floor hit.
After floor hit: gap could go to 0.30 (individual limit), but compound gate blocks first.
