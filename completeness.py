"""
completeness.py
---------------
Data completeness checks for HMIS facility rows.

A facility-month record is "complete" for a variable when at least one of its
source columns (new-format or old-format) IS NOT NULL.

50 completeness variables are split into four sections:
  A  - Inst. Deliveries, ANC & Live Births   (6 fields)
  B1 - Birth to 1-Year Doses                 (23 fields)
  B2 - 1-Year+ & Booster Doses               (15 fields)
  C  - AEFI & Sessions                       (6 fields)

Column definitions store either:
  - A single string:       one column name  (checked with col IS NOT NULL)
  - A tuple of two strings: a COALESCE pair (checked with col_a IS NOT NULL OR col_b IS NOT NULL)

This avoids COALESCE entirely, sidestepping BigQuery type-mismatch errors
when old and new format columns have different data types (FLOAT64 vs STRING).

Public API
----------
  build_completeness_sql(geo_col, geo_code, month_filter, group_by) -> str
  format_completeness_response(spec, results)                        -> str
  prepare_completeness_chart(spec, results)                          -> (dict, list)
"""

from datetime import datetime

_TABLE = (
    "`biharhmisdatafordvctool.BH_HMIS_Data"
    ".BH_HMIS_Facility_Level_Data_with_all_Targets`"
)

# ---------------------------------------------------------------------------
# Column definitions
# Each value is either:
#   str        -> single column  : checked as   col IS NOT NULL
#   (str, str) -> COALESCE pair  : checked as   (col_a IS NOT NULL OR col_b IS NOT NULL)
# ---------------------------------------------------------------------------

_COLS_A = {
    "Td1":     (
        "_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1",
        "_1_2_1_number_of_pw_given_tt1_td1",
    ),
    "Td2":     (
        "_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2",
        "_1_2_2_number_of_pw_given_tt2_td2",
    ),
    "TdB":     (
        "_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster",
        "_1_2_3_number_of_pw_given_tt_booster_td_booster",
    ),
    "InstDel": "_2_2_number_of_institutional_deliveries_conducted_including_c_sections",
    "LB_M":    "m4_pregnancy_outcome_details_of_new_born_4_1_1_a_live_birth_male",
    "LB_F":    "_4_1_1_b_live_birth_female",
}

_COLS_B1 = {
    "HepB0":  (
        "_9_1_10_child_immunisation_hepatitis_b0_birth_dose",
        "_9_1_13_child_immunisation_hepatitis_b0_birth_dose",
    ),
    "OPV0":   (
        "_9_1_6_child_immunisation_opv_0_birth_dose",
        "_9_1_9_child_immunisation_opv_0_birth_dose",
    ),
    "BCG":    "_9_1_2_child_immunisation_bcg",
    "Penta1": (
        "_9_1_3_child_immunisation_pentavalent_1",
        "_9_1_6_child_immunisation_pentavalent_1",
    ),
    "OPV1":   (
        "_9_1_7_child_immunisation_opv1",
        "_9_1_10_child_immunisation_opv1",
    ),
    "IPV1":   (
        "_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1",
        "_9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1",
    ),
    "RVV1":   (
        "_9_1_13_child_immunisation_rotavirus_1",
        "_9_1_19_child_immunisation_rotavirus_1",
    ),
    "PCV1":   (
        "_9_1_16_child_immunisation_pcv1",
        "_9_1_22_child_immunisation_pcv_1",
    ),
    "Penta2": (
        "_9_1_4_child_immunisation_pentavalent_2",
        "_9_1_7_child_immunisation_pentavalent_2",
    ),
    "OPV2":   (
        "_9_1_8_child_immunisation_opv2",
        "_9_1_11_child_immunisation_opv2",
    ),
    "RVV2":   (
        "_9_1_14_child_immunisation_rotavirus_2",
        "_9_1_20_child_immunisation_rotavirus_2",
    ),
    "Penta3": (
        "_9_1_5_child_immunisation_pentavalent_3",
        "_9_1_8_child_immunisation_pentavalent_3",
    ),
    "OPV3":   (
        "_9_1_9_child_immunisation_opv3",
        "_9_1_12_child_immunisation_opv3",
    ),
    "IPV2":   (
        "_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2",
        "_9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2",
    ),
    "RVV3":   (
        "_9_1_15_child_immunisation_rotavirus_3",
        "_9_1_21_child_immunisation_rotavirus_3",
    ),
    "PCV2":   (
        "_9_1_17_child_immunisation_pcv2",
        "_9_1_23_child_immunisation_pcv_2",
    ),
    "MR1_9m": (
        "_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose",
        "_9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose",
    ),
    "IPV3":   (
        "_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3",
        "_9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3",
    ),
    "PCVB":   (
        "_9_2_4_child_immunisation_pcv_booster",
        "_9_1_24_child_immunisation_pcv_booster",
    ),
    "FIC_M":  (
        "_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male",
        "_9_2_4_a_children_aged_between_9_and_11_months_fully_immunized_male",
    ),
    "FIC_F":  (
        "_9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female",
        "_9_2_4_b_children_aged_between_9_and_11_months_fully_immunized_female",
    ),
    "VitA1":  "_9_8_1_child_immunisation_vitamin_a_dose_1",
    "JE1_9m": "_9_2_3_child_immunisation_9_11months_je_1st_dose",
}

_COLS_B2 = {
    "MR1_del":  (
        "_9_3_1_child_immunisation_after_12_months_delayed_vaccination_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose",
        "_9_3_1_child_immunisation_after_12_months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose",
    ),
    "DPT1":     "_9_3_3_child_immunisation_dpt_1_after_12_months_of_age_delayed_vaccination",
    "DPT2":     "_9_3_4_child_immunisation_dpt_2_after_12_months_of_age_delayed_vaccination",
    "DPT3":     "_9_3_5_child_immunisation_dpt_3_after_12_months_of_age_delayed_vaccination",
    "DPTB_2yr": "_9_3_6_child_immunisation_dpt_booster_after_24_months_of_age_delayed_vaccination",
    "OPVB_2yr": "_9_3_7_child_immunisation_opv_booster_after_24_months_of_age_delayed_vaccination",
    "MR2_16m":  (
        "_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months",
        "_9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months",
    ),
    "DPTB1":    (
        "_9_4_2_child_immunisation_dpt_1st_booster",
        "_9_4_3_child_immunisation_dpt_1st_booster",
    ),
    "OPVB":     (
        "_9_4_3_child_immunisation_opv_booster",
        "_9_4_4_child_immunisation_opv_booster",
    ),
    "DPTB2_5y": "_9_5_2_children_more_than_5_years_received_dpt5_2nd_booster",
    "Td10":     (
        "_9_5_3_children_more_than_10_years_received_td10_tetanus_diptheria10",
        "_9_5_3_children_more_than_10_years_received_tt10_td10",
    ),
    "Td16":     (
        "_9_5_4_children_more_than_16_years_received_td16_tetanus_diptheria16",
        "_9_5_4_children_more_than_16_years_received_tt16_td16",
    ),
    "JE1_1yr":  (
        "_9_3_2_child_immunisation_after_12_months_delayed_vaccination_je_1st_dose",
        "_9_3_3_child_immunisation_after_12_months_je_1st_dose",
    ),
    "JEB_2yr":  "_9_3_8_child_immunisation_je_booster_after_24_months_of_age_delayed_vaccination",
    "JE2_16m":  (
        "_9_4_4_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine_2nd_dose_16_24_months",
        "_9_4_6_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine",
    ),
}

_COLS_C = {
    "AEFI_Min": (
        "_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc",
        "_9_6_1_number_of_cases_of_aefi_abscess",
    ),
    "AEFI_Sev": (
        "_9_6_2_number_of_cases_of_aefi_severe_eg_anaphylaxis_fever_102_degrees_not_requiring_hospitalization_etc",
        "_9_6_2_number_of_cases_of_aefi_death",
    ),
    "AEFI_Ser": (
        "_9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc",
        "_9_6_3_number_of_cases_of_aefi_others",
    ),
    "AEFI_D":   "_9_6_3_a_out_of_number_of_cases_of_aefi_serious_total_number_of_aefi_deaths",
    "SessP":    "_9_7_1_immunisation_sessions_planned",
    "SessH":    "_9_7_2_immunisation_sessions_held",
}

_A_COUNT  = len(_COLS_A)   # 6
_B1_COUNT = len(_COLS_B1)  # 23
_B2_COUNT = len(_COLS_B2)  # 15
_C_COUNT  = len(_COLS_C)   # 6
_TOTAL    = _A_COUNT + _B1_COUNT + _B2_COUNT + _C_COUNT  # 50


# ---------------------------------------------------------------------------
# SQL builder helpers
# ---------------------------------------------------------------------------

def _null_check(col_def) -> str:
    """
    Return a SQL boolean expression that is TRUE when the variable has data.

    For a single column:  col IS NOT NULL
    For a pair (a, b):   (a IS NOT NULL OR b IS NOT NULL)
    """
    if isinstance(col_def, tuple):
        return f"({col_def[0]} IS NOT NULL OR {col_def[1]} IS NOT NULL)"
    return f"{col_def} IS NOT NULL"


def _section_sum(cols: dict) -> str:
    """
    Build the SQL expression that sums completeness flags for one section.
    Returns e.g.:
      (
        IF(col_a IS NOT NULL OR col_b IS NOT NULL, 1, 0) +
        IF(col_c IS NOT NULL, 1, 0) +
        ...
      )
    """
    checks = [f"    IF({_null_check(v)}, 1, 0)" for v in cols.values()]
    return "(\n" + " +\n".join(checks) + "\n  )"


# ---------------------------------------------------------------------------
# SQL builder (public)
# ---------------------------------------------------------------------------

def build_completeness_sql(
    geo_col: str,
    geo_code: int,
    month_filter: str,
    group_by: str,  # "summary" | "trend" | "facility"
) -> str:
    """
    Build a BigQuery SQL query that computes completeness percentages for the
    four sections and overall, filtered by geography and time period.

    Parameters
    ----------
    geo_col       : Column to filter on, e.g. "sub_district_ulb_code"
    geo_code      : Numeric code value
    month_filter  : SQL WHERE fragment, e.g. "Month = '2026-01-01'"
    group_by      : "summary"  -> single aggregate row for the period
                    "trend"    -> one row per Month (time-series)
                    "facility" -> one row per facility ordered by completeness

    Implementation note
    -------------------
    Avoids COALESCE entirely to prevent FLOAT64/STRING type-mismatch errors.
    Instead, each variable is checked with:
      IF(col IS NOT NULL, 1, 0)                                 (single column)
      IF(col_a IS NOT NULL OR col_b IS NOT NULL, 1, 0)          (pair)
    This is semantically identical to checking COALESCE IS NOT NULL but
    works regardless of column data types.
    """
    a_sum  = _section_sum(_COLS_A)
    b1_sum = _section_sum(_COLS_B1)
    b2_sum = _section_sum(_COLS_B2)
    c_sum  = _section_sum(_COLS_C)

    if group_by == "facility":
        extra_cols   = "  facility_name,\n  facility_code,"
        group_clause = "GROUP BY facility_name, facility_code"
        order_clause = "ORDER BY overall_completeness_pct"
    elif group_by == "trend":
        extra_cols   = "  Month,"
        group_clause = "GROUP BY Month"
        order_clause = "ORDER BY Month"
    else:  # summary
        extra_cols   = ""
        group_clause = ""
        order_clause = ""

    sql = f"""WITH agg AS (
  SELECT
    Month,
    facility_name,
    facility_code,
    {a_sum}  AS a_row_sum,
    {b1_sum} AS b1_row_sum,
    {b2_sum} AS b2_row_sum,
    {c_sum}  AS c_row_sum
  FROM {_TABLE}
  WHERE infacility_or_outreach_or_total = 'Total'
    AND {geo_col} = {geo_code}
    AND {month_filter}
),
totals AS (
  SELECT *,
    a_row_sum + b1_row_sum + b2_row_sum + c_row_sum AS total_row_sum
  FROM agg
)
SELECT
{extra_cols}
  COUNT(*) AS facility_month_records,
  COUNTIF(total_row_sum = {_TOTAL}) AS fully_complete_facilities,
  ROUND(COUNTIF(total_row_sum = {_TOTAL}) * 100.0 / COUNT(*), 1) AS pct_fully_complete,
  COUNTIF(a_row_sum  = {_A_COUNT})  AS section_a_fully_complete,
  ROUND(COUNTIF(a_row_sum  = {_A_COUNT})  * 100.0 / COUNT(*), 1) AS section_a_pct_fully,
  COUNTIF(b1_row_sum = {_B1_COUNT}) AS section_b1_fully_complete,
  ROUND(COUNTIF(b1_row_sum = {_B1_COUNT}) * 100.0 / COUNT(*), 1) AS section_b1_pct_fully,
  COUNTIF(b2_row_sum = {_B2_COUNT}) AS section_b2_fully_complete,
  ROUND(COUNTIF(b2_row_sum = {_B2_COUNT}) * 100.0 / COUNT(*), 1) AS section_b2_pct_fully,
  COUNTIF(c_row_sum  = {_C_COUNT})  AS section_c_fully_complete,
  ROUND(COUNTIF(c_row_sum  = {_C_COUNT})  * 100.0 / COUNT(*), 1) AS section_c_pct_fully
FROM totals
{group_clause}
{order_clause}
LIMIT 100"""

    return sql.strip()


# ---------------------------------------------------------------------------
# Response formatter
# ---------------------------------------------------------------------------

def _fmt_pct(val) -> str:
    if val is None:
        return "-"
    return f"{val}%"


def _label(val) -> str:
    if val is None:
        return ""
    if val >= 90:
        return " (Good)"
    if val >= 70:
        return " (Moderate)"
    return " (Needs attention)"


def format_completeness_response(spec: dict, results: list[dict]) -> str:
    """Convert completeness query results into a WhatsApp-ready string."""
    group_by = spec.get("group_by", "summary")

    if not results:
        return (
            "No data found for that completeness query.\n"
            "The location or time period may have no records."
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    if group_by == "summary":
        r       = results[0]
        records = r.get("facility_month_records", "?")

        def _row(label, count, pct):
            count_str = str(count) if count is not None else "?"
            return (
                f"{label}: {count_str} out of {records} "
                f"({_fmt_pct(pct)}){_label(pct)}"
            )

        lines = [
            "*Data Completeness Report*",
            f"Facility-month records checked: {records}\n",
            "*% Facilities with All Fields Complete*",
            _row(
                "All 50 fields",
                r.get("fully_complete_facilities"),
                r.get("pct_fully_complete"),
            ),
            _row(
                "Section A - ANC/Deliveries (6 fields)",
                r.get("section_a_fully_complete"),
                r.get("section_a_pct_fully"),
            ),
            _row(
                "Section B1 - Birth to 1-Year Doses (23 fields)",
                r.get("section_b1_fully_complete"),
                r.get("section_b1_pct_fully"),
            ),
            _row(
                "Section B2 - 1-Year+ & Booster Doses (15 fields)",
                r.get("section_b2_fully_complete"),
                r.get("section_b2_pct_fully"),
            ),
            _row(
                "Section C - AEFI & Sessions (6 fields)",
                r.get("section_c_fully_complete"),
                r.get("section_c_pct_fully"),
            ),
        ]

        section_vals = {
            "Section A (ANC/Deliveries)":        r.get("section_a_pct_fully"),
            "Section B1 (Birth Doses)":           r.get("section_b1_pct_fully"),
            "Section B2 (1yr+ Doses)":            r.get("section_b2_pct_fully"),
            "Section C (AEFI/Sessions)":          r.get("section_c_pct_fully"),
        }
        valid = {k: v for k, v in section_vals.items() if v is not None}
        if valid:
            weakest_name = min(valid, key=valid.get)
            weakest_val  = valid[weakest_name]
            if weakest_val < 90:
                lines.append(
                    f"\n_{weakest_name} has the fewest fully-complete facilities "
                    f"at {weakest_val}% - check if facilities are leaving these fields blank._"
                )

        return "\n".join(lines)

    # ── Trend ─────────────────────────────────────────────────────────────────
    elif group_by == "trend":
        header = (
            f"{'Month':<10} {'All 50':>6} {'Sec A':>6} "
            f"{'Sec B1':>7} {'Sec B2':>7} {'Sec C':>6}"
        )
        separator = "-" * 48
        rows_txt = []
        for r in results:
            raw_month = str(r.get("Month", ""))[:10]
            try:
                m = datetime.strptime(raw_month, "%Y-%m-%d")
                month_str = m.strftime("%b %Y")
            except Exception:
                month_str = raw_month
            rows_txt.append(
                f"{month_str:<10} "
                f"{_fmt_pct(r.get('pct_fully_complete')):>6} "
                f"{_fmt_pct(r.get('section_a_pct_fully')):>6} "
                f"{_fmt_pct(r.get('section_b1_pct_fully')):>7} "
                f"{_fmt_pct(r.get('section_b2_pct_fully')):>7} "
                f"{_fmt_pct(r.get('section_c_pct_fully')):>6}"
            )

        table = "```\n" + header + "\n" + separator + "\n" + "\n".join(rows_txt) + "\n```"

        vals = [r.get("pct_fully_complete") for r in results
                if r.get("pct_fully_complete") is not None]
        avg_line = ""
        if vals:
            avg = round(sum(vals) / len(vals), 1)
            avg_line = f"\n*Average % facilities fully complete: {avg}%*{_label(avg)}"

        return (
            f"*Data Completeness - Monthly Trend*\n"
            f"_Values = % facilities with ALL fields complete in that section_\n"
            f"{table}{avg_line}"
        )

    # ── Facility ──────────────────────────────────────────────────────────────
    else:
        # Aggregate fully-complete counts across all facility rows
        total_records = sum(r.get("facility_month_records", 0) or 0 for r in results)
        total_fully   = sum(r.get("fully_complete_facilities", 0) or 0 for r in results)
        pct_fully_all = (
            round(total_fully * 100.0 / total_records, 1) if total_records else None
        )
        summary_line = (
            f"*Fully complete (all 50 fields):* "
            f"{total_fully} of {total_records} records "
            f"({_fmt_pct(pct_fully_all)})\n"
        )

        display   = results[:25]
        header    = (
            f"{'Facility':<28} {'All50':>6} "
            f"{'A':>5} {'B1':>5} {'B2':>5} {'C':>5}"
        )
        separator = "-" * 57
        rows_txt  = []
        for r in display:
            name = str(r.get("facility_name", "?"))
            if len(name) > 26:
                name = name[:25] + "."
            rows_txt.append(
                f"{name:<28} "
                f"{_fmt_pct(r.get('pct_fully_complete')):>6} "
                f"{_fmt_pct(r.get('section_a_pct_fully')):>5} "
                f"{_fmt_pct(r.get('section_b1_pct_fully')):>5} "
                f"{_fmt_pct(r.get('section_b2_pct_fully')):>5} "
                f"{_fmt_pct(r.get('section_c_pct_fully')):>5}"
            )

        table  = "```\n" + header + "\n" + separator + "\n" + "\n".join(rows_txt) + "\n```"
        suffix = (f"\n_(showing 25 of {len(results)} facilities)_"
                  if len(results) > 25 else "")
        return f"*Completeness by Facility*\n{summary_line}{table}{suffix}"


# ---------------------------------------------------------------------------
# Chart preparation
# ---------------------------------------------------------------------------

def prepare_completeness_chart(spec: dict, results: list[dict]) -> tuple[dict, list]:
    """
    Return (chart_spec, chart_rows) ready for chart_module.generate_chart().

    summary  -> horizontal bar of the 4 sections (reshapes single-row result)
    trend    -> line chart of overall_completeness_pct over months
    facility -> horizontal bar of per-facility overall completeness
    """
    group_by = spec.get("group_by", "summary")

    if not results:
        return {"should_visualize": False}, []

    if group_by == "summary":
        r = results[0]
        chart_rows = [
            {"Section": "All 50 fields",          "% Fully Complete": r.get("pct_fully_complete",  0)},
            {"Section": "A: ANC/Deliveries (6)",  "% Fully Complete": r.get("section_a_pct_fully", 0)},
            {"Section": "B1: Birth Doses (23)",   "% Fully Complete": r.get("section_b1_pct_fully", 0)},
            {"Section": "B2: 1yr+ Doses (15)",    "% Fully Complete": r.get("section_b2_pct_fully", 0)},
            {"Section": "C: AEFI/Sessions (6)",   "% Fully Complete": r.get("section_c_pct_fully",  0)},
        ]
        chart_spec = {
            "should_visualize": True,
            "chart_type":       "horizontal_bar",
            "title":            "% Facilities Fully Complete by Section",
            "x_column":         "Section",
            "y_columns":        ["% Fully Complete"],
            "y_labels":         ["% Fully Complete"],
            "x_label":          "Section",
            "y_label":          "% Facilities Fully Complete",
        }
        return chart_spec, chart_rows

    elif group_by == "trend":
        chart_spec = {
            "should_visualize": True,
            "chart_type":       "line",
            "title":            "% Facilities Fully Complete - Monthly Trend",
            "x_column":         "Month",
            "y_columns":        ["pct_fully_complete"],
            "y_labels":         ["All 50 fields (%)"],
            "x_label":          "Month",
            "y_label":          "% Facilities Fully Complete",
        }
        return chart_spec, results

    else:  # facility
        chart_spec = {
            "should_visualize": True,
            "chart_type":       "horizontal_bar",
            "title":            "% Fully Complete by Facility",
            "x_column":         "facility_name",
            "y_columns":        ["pct_fully_complete"],
            "y_labels":         ["All 50 fields (%)"],
            "x_label":          "Facility",
            "y_label":          "% Fully Complete",
        }
        return chart_spec, results[:25]
