"""
pre.py
------
Probable Reporting Errors (PRE) checks for HMIS facility data.

A PRE is flagged for a facility-month record when a pair of indicators that should
be equal (co-administered antigens at the same visit) are reported differently, or
when a logically impossible relationship is observed.

N = 16 checks across 5 groups:
  A1 (4 checks): Penta1 discrepancies at 6 weeks   — vs OPV1, IPV1, RVV1, PCV1
  A2 (2 checks): Penta2 discrepancies at 10 weeks  — vs OPV2, RVV2
  A3 (4 checks): Penta3 discrepancies at 14 weeks  — vs OPV3, IPV2, RVV3, PCV2
  A4 (3 checks): MR1(9-11m) discrepancies          — vs IPV3, PCV-B, JE1
  B  (3 checks): Logical impossibilities            — Penta3>Penta1, MR2>MR1,
                                                      sessions>0 but AEFI-minor=0

Primary metric: pct_pre = ROUND(AVG(pre_total) * 100.0 / 16, 1)
  = average fraction of 16 PRE categories violated per facility-month record.

Public API
----------
  build_pre_sql(geo_col, geo_code, month_filter, group_by) -> str
  format_pre_response(spec, results)                        -> str
  prepare_pre_chart(spec, results)                          -> (dict, list)
"""

from datetime import datetime

_TABLE = (
    "`biharhmisdatafordvctool.BH_HMIS_Data"
    ".BH_HMIS_Facility_Level_Data_with_all_Targets`"
)

_N = 16  # total number of PRE checks

# ---------------------------------------------------------------------------
# Indicator definitions — each maps a short alias to its COALESCE expression.
# Pairs are (newer_col, older_col); single-element tuples are non-paired cols.
# All columns are FLOAT64, so COALESCE is safe (no type mismatch).
# ---------------------------------------------------------------------------

_INDICATORS: dict[str, tuple] = {
    "v_penta1": (
        "_9_1_3_child_immunisation_pentavalent_1",
        "_9_1_6_child_immunisation_pentavalent_1",
    ),
    "v_penta2": (
        "_9_1_4_child_immunisation_pentavalent_2",
        "_9_1_7_child_immunisation_pentavalent_2",
    ),
    "v_penta3": (
        "_9_1_5_child_immunisation_pentavalent_3",
        "_9_1_8_child_immunisation_pentavalent_3",
    ),
    "v_opv1": (
        "_9_1_7_child_immunisation_opv1",
        "_9_1_10_child_immunisation_opv1",
    ),
    "v_opv2": (
        "_9_1_8_child_immunisation_opv2",
        "_9_1_11_child_immunisation_opv2",
    ),
    "v_opv3": (
        "_9_1_9_child_immunisation_opv3",
        "_9_1_12_child_immunisation_opv3",
    ),
    "v_ipv1": (
        "_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1",
        "_9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1",
    ),
    "v_ipv2": (
        "_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2",
        "_9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2",
    ),
    "v_ipv3": (
        "_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3",
        "_9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3",
    ),
    "v_rvv1": (
        "_9_1_13_child_immunisation_rotavirus_1",
        "_9_1_19_child_immunisation_rotavirus_1",
    ),
    "v_rvv2": (
        "_9_1_14_child_immunisation_rotavirus_2",
        "_9_1_20_child_immunisation_rotavirus_2",
    ),
    "v_rvv3": (
        "_9_1_15_child_immunisation_rotavirus_3",
        "_9_1_21_child_immunisation_rotavirus_3",
    ),
    "v_pcv1": (
        "_9_1_16_child_immunisation_pcv1",
        "_9_1_22_child_immunisation_pcv_1",
    ),
    "v_pcv2": (
        "_9_1_17_child_immunisation_pcv2",
        "_9_1_23_child_immunisation_pcv_2",
    ),
    "v_pcvb": (
        "_9_2_4_child_immunisation_pcv_booster",
        "_9_1_24_child_immunisation_pcv_booster",
    ),
    "v_mr1": (
        "_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose",
        "_9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose",
    ),
    "v_mr2": (
        "_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months",
        "_9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months",
    ),
    "v_je1": ("_9_2_3_child_immunisation_9_11months_je_1st_dose",),
    "v_aefi_minor": (
        "_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc",
        "_9_6_1_number_of_cases_of_aefi_abscess",
    ),
    "v_sess_held": ("_9_7_2_immunisation_sessions_held",),
}

# ---------------------------------------------------------------------------
# PRE check definitions
# Each tuple: (flag_alias, group, label, condition)
#   condition — SQL expression that is TRUE when the error is present.
#               No IS NOT NULL guards; SQL NULL-comparison semantics apply
#               (NULL op anything = NULL = falsy, so missing data never fires).
# ---------------------------------------------------------------------------

_CHECKS: list[tuple] = [
    # A1: Penta1 discrepancies at 6 weeks
    ("pre_a1a", "A1", "Penta1 ≠ OPV1 (6wk)",    "v_penta1 != v_opv1"),
    ("pre_a1b", "A1", "Penta1 ≠ IPV1 (6wk)",    "v_penta1 != v_ipv1"),
    ("pre_a1c", "A1", "Penta1 ≠ RVV1 (6wk)",    "v_penta1 != v_rvv1"),
    ("pre_a1d", "A1", "Penta1 ≠ PCV1 (6wk)",    "v_penta1 != v_pcv1"),
    # A2: Penta2 discrepancies at 10 weeks
    ("pre_a2a", "A2", "Penta2 ≠ OPV2 (10wk)",   "v_penta2 != v_opv2"),
    ("pre_a2b", "A2", "Penta2 ≠ RVV2 (10wk)",   "v_penta2 != v_rvv2"),
    # A3: Penta3 discrepancies at 14 weeks
    ("pre_a3a", "A3", "Penta3 ≠ OPV3 (14wk)",   "v_penta3 != v_opv3"),
    ("pre_a3b", "A3", "Penta3 ≠ IPV2 (14wk)",   "v_penta3 != v_ipv2"),
    ("pre_a3c", "A3", "Penta3 ≠ RVV3 (14wk)",   "v_penta3 != v_rvv3"),
    ("pre_a3d", "A3", "Penta3 ≠ PCV2 (14wk)",   "v_penta3 != v_pcv2"),
    # A4: MR1 discrepancies at 9-11 months
    ("pre_a4a", "A4", "MR1 ≠ IPV3 (9-11m)",     "v_mr1 != v_ipv3"),
    ("pre_a4b", "A4", "MR1 ≠ PCV-B (9-11m)",    "v_mr1 != v_pcvb"),
    ("pre_a4c", "A4", "MR1 ≠ JE1 (9-11m)",      "v_mr1 != v_je1"),
    # B: Logical impossibilities / suspicious patterns
    ("pre_b1",  "B",  "Penta3 > Penta1",         "v_penta3 > v_penta1"),
    ("pre_b2",  "B",  "MR2 > MR1",               "v_mr2 > v_mr1"),
    ("pre_b3",  "B",  "AEFI-minor reported as 0", "v_aefi_minor = 0"),
]

_GROUPS: dict[str, list[str]] = {
    "A1": [c[0] for c in _CHECKS if c[1] == "A1"],
    "A2": [c[0] for c in _CHECKS if c[1] == "A2"],
    "A3": [c[0] for c in _CHECKS if c[1] == "A3"],
    "A4": [c[0] for c in _CHECKS if c[1] == "A4"],
    "B":  [c[0] for c in _CHECKS if c[1] == "B"],
}


# ---------------------------------------------------------------------------
# SQL builder helpers
# ---------------------------------------------------------------------------

def _indicator_expr(alias: str, cols: tuple) -> str:
    if len(cols) == 1:
        return f"  SAFE_CAST({cols[0]} AS FLOAT64) AS {alias}"
    return (
        f"  COALESCE(SAFE_CAST({cols[0]} AS FLOAT64),"
        f" SAFE_CAST({cols[1]} AS FLOAT64)) AS {alias}"
    )


def _flag_expr(flag: str, condition: str) -> str:
    return f"    IF({condition}, 1, 0) AS {flag}"


def _group_countif(flags: list[str]) -> str:
    sum_expr = " + ".join(flags)
    return f"ROUND(COUNTIF({sum_expr} > 0) * 100.0 / COUNT(*), 1)"


# ---------------------------------------------------------------------------
# SQL builder (public)
# ---------------------------------------------------------------------------

def build_pre_sql(
    geo_col: str,
    geo_code: int,
    month_filter: str,
    group_by: str,  # "summary" | "trend" | "facility"
) -> str:
    """
    Build a BigQuery SQL query that computes PRE rates for the 16 checks,
    filtered by geography and time period.

    Parameters
    ----------
    geo_col       : Column to filter on, e.g. "sub_district_ulb_code"
    geo_code      : Numeric code value
    month_filter  : SQL WHERE fragment, e.g. "Month = '2026-01-01'"
    group_by      : "summary"  -> single aggregate row
                    "trend"    -> one row per Month
                    "facility" -> one row per facility
    """
    # CTE 1: indicators — COALESCE old/new format into clean aliases
    ind_lines = ",\n".join(
        _indicator_expr(alias, cols) for alias, cols in _INDICATORS.items()
    )
    indicators_cte = f"""indicators AS (
  SELECT
    Month,
    facility_name,
    facility_code,
{ind_lines}
  FROM {_TABLE}
  WHERE infacility_or_outreach_or_total = 'Total'
    AND {geo_col} = {geo_code}
    AND {month_filter}
)"""

    # CTE 2: pre_flags — 16 IF checks per record
    flag_lines = ",\n".join(
        _flag_expr(flag, condition)
        for flag, _grp, _lbl, condition in _CHECKS
    )
    pre_flags_cte = f"""pre_flags AS (
  SELECT *,
{flag_lines}
  FROM indicators
)"""

    # CTE 3: totals — sum all 16 flags
    flag_names = [c[0] for c in _CHECKS]
    pre_sum = " +\n    ".join(flag_names)
    totals_cte = f"""totals AS (
  SELECT *,
    {pre_sum}
    AS pre_total
  FROM pre_flags
)"""

    # Final SELECT — grouping columns
    if group_by == "facility":
        extra_cols   = "  facility_name,\n  facility_code,"
        group_clause = "GROUP BY facility_name, facility_code"
        order_clause = "ORDER BY pct_pre DESC"
    elif group_by == "trend":
        extra_cols   = "  Month,"
        group_clause = "GROUP BY Month"
        order_clause = "ORDER BY Month"
    else:
        extra_cols   = ""
        group_clause = ""
        order_clause = ""

    # Group-level COUNTIF
    group_metrics = "\n".join(
        f"  {_group_countif(_GROUPS[g])} AS group_{g.lower()}_pct,"
        for g in ("A1", "A2", "A3", "A4", "B")
    )

    # Individual flag rates
    individual_metrics = "\n".join(
        f"  ROUND(COUNTIF({f} = 1) * 100.0 / COUNT(*), 1) AS {f}_pct,"
        for f in flag_names
    )

    select_block = f"""SELECT
{extra_cols}
  COUNT(*) AS facility_month_records,
  ROUND(AVG(pre_total) * 100.0 / {_N}, 1) AS pct_pre,
  COUNTIF(pre_total = 0) AS facilities_zero_pre,
  ROUND(COUNTIF(pre_total = 0) * 100.0 / COUNT(*), 1) AS pct_facilities_zero_pre,
{group_metrics}
{individual_metrics}
FROM totals
{group_clause}
{order_clause}
LIMIT 100"""

    sql = f"WITH {indicators_cte},\n{pre_flags_cte},\n{totals_cte}\n{select_block}"
    return sql.strip()


# ---------------------------------------------------------------------------
# Response formatter
# ---------------------------------------------------------------------------

def _fmt_pct(val) -> str:
    return "-" if val is None else f"{val}%"


def _label_pre(val) -> str:
    if val is None:
        return ""
    if val <= 5:
        return " (Good)"
    if val <= 15:
        return " (Moderate)"
    return " (High — needs attention)"


def format_pre_response(spec: dict, results: list[dict]) -> str:
    """Convert PRE query results into a WhatsApp-ready string."""
    group_by = spec.get("group_by", "summary")

    if not results:
        return (
            "No data found for that PRE query.\n"
            "The location or time period may have no records."
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    if group_by == "summary":
        r       = results[0]
        records = r.get("facility_month_records", "?")
        pct_pre = r.get("pct_pre")
        zero    = r.get("facilities_zero_pre")
        pct_z   = r.get("pct_facilities_zero_pre")

        lines = [
            "*Probable Reporting Errors (PRE) Report*",
            f"Facility-month records: {records}\n",
            f"*Overall PRE rate: {_fmt_pct(pct_pre)}*{_label_pre(pct_pre)}",
            f"_({zero} of {records} facilities had 0 PREs = {_fmt_pct(pct_z)})_\n",
            "*Error rate by group:*",
            f"  A1 (Penta1 vs OPV1/IPV1/RVV1/PCV1 at 6wk): {_fmt_pct(r.get('group_a1_pct'))}",
            f"  A2 (Penta2 vs OPV2/RVV2 at 10wk):           {_fmt_pct(r.get('group_a2_pct'))}",
            f"  A3 (Penta3 vs OPV3/IPV2/RVV3/PCV2 at 14wk): {_fmt_pct(r.get('group_a3_pct'))}",
            f"  A4 (MR1 vs IPV3/PCV-B/JE1 at 9-11m):        {_fmt_pct(r.get('group_a4_pct'))}",
            f"  B  (Penta3>Penta1 / MR2>MR1 / No AEFI):     {_fmt_pct(r.get('group_b_pct'))}",
        ]

        # Flag worst group
        group_vals = {
            "A1 (6wk co-admin)":  r.get("group_a1_pct"),
            "A2 (10wk co-admin)": r.get("group_a2_pct"),
            "A3 (14wk co-admin)": r.get("group_a3_pct"),
            "A4 (9-11m co-admin)":r.get("group_a4_pct"),
            "B (logical errors)": r.get("group_b_pct"),
        }
        valid = {k: v for k, v in group_vals.items() if v is not None}
        if valid:
            worst_name = max(valid, key=valid.get)
            worst_val  = valid[worst_name]
            if worst_val > 5:
                lines.append(
                    f"\n_{worst_name} has the highest PRE rate ({worst_val}%) "
                    f"— check if reporters are entering different counts for co-administered vaccines._"
                )

        return "\n".join(lines)

    # ── Trend ─────────────────────────────────────────────────────────────────
    elif group_by == "trend":
        header    = f"{'Month':<10} {'PRE%':>6} {'A1':>5} {'A2':>5} {'A3':>5} {'A4':>5} {'B':>5}"
        separator = "-" * 44
        rows_txt  = []
        for r in results:
            raw_month = str(r.get("Month", ""))[:10]
            try:
                m = datetime.strptime(raw_month, "%Y-%m-%d")
                month_str = m.strftime("%b %Y")
            except Exception:
                month_str = raw_month
            rows_txt.append(
                f"{month_str:<10} "
                f"{_fmt_pct(r.get('pct_pre')):>6} "
                f"{_fmt_pct(r.get('group_a1_pct')):>5} "
                f"{_fmt_pct(r.get('group_a2_pct')):>5} "
                f"{_fmt_pct(r.get('group_a3_pct')):>5} "
                f"{_fmt_pct(r.get('group_a4_pct')):>5} "
                f"{_fmt_pct(r.get('group_b_pct')):>5}"
            )

        table = "```\n" + header + "\n" + separator + "\n" + "\n".join(rows_txt) + "\n```"

        vals = [r.get("pct_pre") for r in results if r.get("pct_pre") is not None]
        avg_line = ""
        if vals:
            avg = round(sum(vals) / len(vals), 1)
            avg_line = f"\n*Average PRE rate over period: {avg}%*{_label_pre(avg)}"

        return (
            f"*Probable Reporting Errors — Monthly Trend*\n"
            f"_PRE% = avg fraction of 16 error categories violated per facility-month_\n"
            f"{table}{avg_line}"
        )

    # ── Facility ──────────────────────────────────────────────────────────────
    else:
        total_records = sum(r.get("facility_month_records", 0) or 0 for r in results)
        zero_count    = sum(r.get("facilities_zero_pre", 0) or 0 for r in results)
        pct_zero_all  = (
            round(zero_count * 100.0 / total_records, 1) if total_records else None
        )
        summary_line = (
            f"*Facilities with 0 PREs:* {zero_count} of {total_records} "
            f"({_fmt_pct(pct_zero_all)})\n"
        )

        display   = results[:25]
        header    = f"{'Facility':<28} {'PRE%':>6} {'A1':>5} {'A2':>5} {'A3':>5} {'A4':>5} {'B':>5}"
        separator = "-" * 58
        rows_txt  = []
        for r in display:
            name = str(r.get("facility_name", "?"))
            if len(name) > 26:
                name = name[:25] + "."
            rows_txt.append(
                f"{name:<28} "
                f"{_fmt_pct(r.get('pct_pre')):>6} "
                f"{_fmt_pct(r.get('group_a1_pct')):>5} "
                f"{_fmt_pct(r.get('group_a2_pct')):>5} "
                f"{_fmt_pct(r.get('group_a3_pct')):>5} "
                f"{_fmt_pct(r.get('group_a4_pct')):>5} "
                f"{_fmt_pct(r.get('group_b_pct')):>5}"
            )

        table  = "```\n" + header + "\n" + separator + "\n" + "\n".join(rows_txt) + "\n```"
        suffix = (f"\n_(showing 25 of {len(results)} facilities)_"
                  if len(results) > 25 else "")
        return f"*PRE by Facility*\n{summary_line}{table}{suffix}"


# ---------------------------------------------------------------------------
# Chart preparation
# ---------------------------------------------------------------------------

def prepare_pre_chart(spec: dict, results: list[dict]) -> tuple[dict, list]:
    """
    Return (chart_spec, chart_rows) for chart_module.generate_chart().

    summary  -> horizontal bar of the 5 groups
    trend    -> line chart of pct_pre over months
    facility -> horizontal bar of per-facility pct_pre
    """
    group_by = spec.get("group_by", "summary")

    if not results:
        return {"should_visualize": False}, []

    if group_by == "summary":
        r = results[0]
        chart_rows = [
            {"Group": "Overall PRE%",              "% Error": r.get("pct_pre", 0)},
            {"Group": "A1: Penta1 6wk (4 checks)", "% Error": r.get("group_a1_pct", 0)},
            {"Group": "A2: Penta2 10wk (2 checks)","% Error": r.get("group_a2_pct", 0)},
            {"Group": "A3: Penta3 14wk (4 checks)","% Error": r.get("group_a3_pct", 0)},
            {"Group": "A4: MR1 9-11m (3 checks)",  "% Error": r.get("group_a4_pct", 0)},
            {"Group": "B: Logical errors (3)",      "% Error": r.get("group_b_pct",  0)},
        ]
        chart_spec = {
            "should_visualize": True,
            "chart_type":       "horizontal_bar",
            "title":            "PRE Rate by Group",
            "x_column":         "Group",
            "y_columns":        ["% Error"],
            "y_labels":         ["% Error"],
            "x_label":          "Group",
            "y_label":          "% Facilities with Error",
        }
        return chart_spec, chart_rows

    elif group_by == "trend":
        chart_spec = {
            "should_visualize": True,
            "chart_type":       "line",
            "title":            "PRE Rate — Monthly Trend",
            "x_column":         "Month",
            "y_columns":        ["pct_pre"],
            "y_labels":         ["Overall PRE%"],
            "x_label":          "Month",
            "y_label":          "PRE%",
        }
        return chart_spec, results

    else:  # facility
        chart_spec = {
            "should_visualize": True,
            "chart_type":       "horizontal_bar",
            "title":            "PRE Rate by Facility",
            "x_column":         "facility_name",
            "y_columns":        ["pct_pre"],
            "y_labels":         ["PRE%"],
            "x_label":          "Facility",
            "y_label":          "PRE%",
        }
        return chart_spec, results[:25]
