# HMIS WhatsApp Analytics Agent

## Project Purpose
A WhatsApp bot that allows immunization field officers in UP/Bihar to ask plain-language
questions about HMIS data and receive summarized answers. Users are non-technical
government health staff, so all responses must be simple, clear, and in plain English
(or Hindi if requested).

## Tech Stack
- Language: Python 3
- Chat platform: WhatsApp Cloud API (Meta Graph API), via FastAPI webhook
- AI: Anthropic Claude API (claude-sonnet-4-20250514)
- Database: Google BigQuery

## BigQuery Configuration
- Project ID: biharhmisdatafordvctool
- Dataset: BH_HMIS_Data
- Main Table: BH_HMIS_Facility_Level_Data_with_all_Targets
- Credentials File: service_account.json
- Full table path: `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`

---

## BigQuery Table Schema

The main table has 283 columns organized into the following groups.

### Geography & Facility Identifiers
- `Month` (STRING): Reporting month in YYYY-MM-DD format (first day of month)
- `state_code` (INTEGER): Numeric state code (e.g., 10 = Bihar)
- `state` (STRING): State name (e.g., "Bihar", "Uttar Pradesh")
- `district_code` (INTEGER): Numeric district code
- `district_name` (STRING): District name (e.g., "Begusarai")
- `district_category` (STRING): District classification (e.g., "Aspirational", "Mission Parivar Vikas (MPV)")
- `sub_district_ulb_code` (INTEGER): Sub-district or Urban Local Body code
- `sub_district_ulb_name` (STRING): Sub-district or ULB name
- `block_code` (INTEGER): Block code — DO NOT use for filtering (data anomalies in HMIS export)
- `block_name` (STRING): Block name — DO NOT use for filtering (data anomalies); use sub_district_ulb_name / sub_district_ulb_code instead for all block-level queries
- `health_block_code` (FLOAT): Health block code (may differ from revenue block)
- `health_block_name` (STRING): Health block name
- `local_assembly_constituency` (STRING): MLA constituency name
- `parliament_constituency` (STRING): MP constituency name
- `facility_type` (STRING): Type of health facility (e.g., "AAM-PHC/PHC", "Sub Centre")
- `facility_code` (INTEGER): Unique facility code
- `facility_name` (STRING): Name of health facility
- `ulb_code` (FLOAT): Urban Local Body code (if applicable)
- `ulb_name` (STRING): Urban Local Body name (if applicable)
- `category` (STRING): Facility category (e.g., "Delivery Point", "Under Health Statistics")
- `sanctioned_bed_count` (INTEGER): Number of sanctioned beds
- `functional_bed_count` (INTEGER): Number of functional beds
- `facility_nin_number` (STRING): National Identification Number of the facility
- `rural_urban` (STRING): Rural or Urban classification
- `ownership` (STRING): Ownership type (e.g., "Public", "Private")
- `physical_notional` (STRING): Whether facility is physical or notional
- `status` (STRING): Facility status (e.g., "Open", "Closed")
- `active_from_date` (STRING): Date facility became active
- `active_to_date` (STRING): Date facility became inactive (null if still active)
- `upload_date` (STRING): Date data was uploaded to HMIS
- `facility_sub_type` (STRING): Sub-type of facility (e.g., "24*7 AAM-PHC (R)")
- `infacility_or_outreach_or_total` (STRING): Whether data is for in-facility, outreach, or combined total
- `providing_outreach_service` (BOOLEAN): Whether facility provides outreach services
- `ownership_classification` (STRING): Ownership classification detail
- `format_type` (STRING): HMIS format type used for reporting (e.g., "PHC Format")
- `administrative_body` (STRING): Governing administrative body
- `providing_ayush_services` (STRING): Whether AYUSH services are provided
- `ayush_stream` (STRING): AYUSH stream (Ayurveda, Yoga, Unani, Siddha, Homeopathy)
- `co_located_ayush_facility` (STRING): Co-located AYUSH facility name
- `hfr_id` (STRING): Health Facility Registry ID (national unique identifier, e.g., "IN1010012778")

### Module 1: Ante-Natal Care (ANC) — Prefix: m1_ or _1_
- `m1_ante_natal_care_anc_1_1_total_number_of_pregnant_women_registered_for_anc` (FLOAT): Total PW registered for ANC (cumulative, older format)
- `m1_ante_natal_care_anc_1_1_total_number_of_new_pregnant_women_registered_for_anc` (FLOAT): New PW registered for ANC (updated format)
- `_1_1_1_out_of_the_total_anc_registered_number_registered_within_1st_trimester_within_12_weeks` (FLOAT): PW registered within first trimester (≤12 weeks) — older format
- `_1_1_1_out_of_the_total_new_anc_registered_number_registered_within_1st_trimester_within_12_weeks` (FLOAT): New PW registered in first trimester — updated format
- `_1_1_2_total_anc_footfall_cases_old_cases_new_registration_attended` (FLOAT): Total ANC visits including old and new cases
- `_1_1_a_out_of_total_number_of_new_pregnant_women_registered_with_age_15_years` (FLOAT): New PW registered aged <15 years
- `_1_1_b_out_of_total_number_of_new_pregnant_women_registered_with_age_15_19_years` (FLOAT): New PW registered aged 15–19 years
- `_1_1_c_out_of_total_number_of_new_pregnant_women_registered_with_age_19_to_49_years` (FLOAT): New PW registered aged 19–49 years
- `_1_1_d_out_of_total_number_of_new_pregnant_women_registered_with_age_49_years` (FLOAT): New PW registered aged >49 years
- `_1_2_1_number_of_pw_given_tt1_td1` (FLOAT): PW given TT1/TD1 vaccine — older format
- `_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1` (FLOAT): PW given TD1 — updated format
- `_1_2_2_number_of_pw_given_tt2_td2` (FLOAT): PW given TT2/TD2 — older format
- `_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2` (FLOAT): PW given TD2 — updated format
- `_1_2_3_number_of_pw_given_tt_booster_td_booster` (FLOAT): PW given TT/TD booster — older format
- `_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster` (FLOAT): PW given TD booster — updated format
- `_1_2_4_number_of_pw_provided_full_course_180_iron_folic_acid_ifa_tablets` (FLOAT): PW given full course of 180 IFA tablets
- `_1_2_5_number_of_pw_provided_full_course_360_calcium_tablets` (FLOAT): PW given full course of 360 calcium tablets
- `_1_2_6_number_of_pw_given_one_albendazole_tablet_after_1st_trimester` (FLOAT): PW given albendazole after 1st trimester
- `_1_2_7_number_of_pw_received_4_or_more_anc_check_ups` (FLOAT): PW who received 4+ ANC check-ups
- `_1_2_8_number_of_pw_given_anc_corticosteroids_in_pre_term_labour` (FLOAT): PW given corticosteroids for pre-term labour
- `_1_3_1_new_cases_of_pw_with_hypertension_detected` (FLOAT): New PW with hypertension detected
- `_1_3_1_a_out_of_the_new_cases_of_pw_with_hypertension_detected_cases_managed_at_institution` (FLOAT): Hypertension cases managed at institution — older format
- `_1_3_1_a_number_of_pw_with_hypertension_managed_at_institution` (FLOAT): Hypertension cases managed — updated format
- `_1_3_2_number_of_pw_with_hypertension_managed_at_institution` (FLOAT): Hypertension managed (updated format, note: same index as eclampsia in older format)
- `_1_3_3_number_of_new_pre_eclampsia_eclampsia_cases_identified` (FLOAT): New pre-eclampsia/eclampsia cases identified
- `_1_3_4_number_of_pre_eclampsia_eclampsia_cases_managed_during_anc` (FLOAT): Pre-eclampsia/eclampsia cases managed during ANC
- `_1_3_5_number_of_eclampsia_cases_managed_during_delivery` (FLOAT): Eclampsia cases managed during delivery — updated format
- `_1_3_2_number_of_eclampsia_cases_managed_during_delivery` (FLOAT): Eclampsia during delivery — older format
- `_1_4_1_number_of_pw_tested_for_haemoglobin_hb_4_or_more_than_4_times_for_respective_ancs` (FLOAT): PW tested for Hb 4+ times — older format
- `_1_4_2_number_of_pw_tested_for_haemoglobin_hb` (FLOAT): PW tested for Hb — updated format
- `_1_4_2_number_of_pw_having_hb_level_11_out_of_total_tested_cases_7_1_to_10_9` (FLOAT): PW with mild-moderate anaemia Hb 7.1–10.9 g/dL — older format
- `_1_4_2_number_of_pw_having_hb_level_11_7_1_to_10_9_g_dl_out_of_total_tested_cases` (FLOAT): PW with mild-moderate anaemia — updated format
- `_1_4_3_number_of_pw_having_hb_level_7_out_of_total_tested_cases` (FLOAT): PW with severe anaemia Hb <7 g/dL — older format
- `_1_4_3_number_of_pw_having_hb_level_7_g_dl_out_of_total_tested_cases` (FLOAT): PW with severe anaemia — updated format
- `_1_4_4_number_of_pw_having_severe_anaemia_hb_7_treated` (FLOAT): PW treated for severe anaemia — older format
- `_1_4_4_number_of_pw_treated_for_severe_anaemia_hb_7g_dl_out_of_total_tested_cases` (FLOAT): PW treated for severe anaemia — updated format
- `_1_4_5_number_of_pw_treated_for_severe_anaemia_hb_7g_dl_including_referred_in_cases` (FLOAT): PW treated for severe anaemia including referred-in cases — newer format
- `_1_5_1_number_of_pw_tested_for_blood_sugar_using_ogtt_oral_glucose_tolerance_test` (FLOAT): PW tested for gestational diabetes (OGTT)
- `_1_5_2_number_of_pw_tested_positive_for_gdm` (FLOAT): PW testing positive for GDM — older format
- `_1_5_2_number_of_pw_tested_positive_for_gdm_out_of_total_ogtt_oral_glucose_tolerance_test_conducted` (FLOAT): PW GDM positive — updated format
- `_1_5_3_number_of_pw_given_insulin_out_of_total_tested_positive_for_gdm` (FLOAT): PW given insulin for GDM — older format
- `_1_5_3_number_of_gdm_positive_pw_managed_with_insulin_metformin_out_of_total_tested_positive_for_gdm` (FLOAT): PW managed with insulin/metformin — updated format
- `_1_5_4_number_of_pw_given_metformin_out_of_total_tested_positive_for_gdm` (FLOAT): PW given metformin for GDM
- `_1_6_1_a_number_of_pw_tested_using_poc_test_for_syphilis` (FLOAT): PW tested for syphilis using POC test — older format
- `_1_6_1_a_number_of_pregnant_direct_in_labor_dil_women_screened_tested_with_vdrl_rpr_tpha_rdt_poc_for_syphilis` (FLOAT): PW in labour screened for syphilis — updated format
- `_1_6_1_b_out_of_above_number_of_pw_found_sero_positive_for_syphilis` (FLOAT): PW seropositive for syphilis — older format
- `_1_6_1_b_number_of_pregnant_dil_women_found_seropositive_for_syphilis_by_vdrl_rpr_tpha_rdt_poc_test` (FLOAT): DIL PW seropositive for syphilis — updated format
- `_1_6_1_c_number_of_pregnant_dil_women_found_syphilis_seropositive_and_given_treatment_with_injection_benzathine_penicillin_im` (FLOAT): Syphilis positive PW treated
- `_1_6_2_a_number_of_pregnant_women_tested_for_syphilis` (FLOAT): PW tested for syphilis (general)
- `_1_6_2_b_number_of_pregnant_women_tested_found_sero_positive_for_syphilis` (FLOAT): PW seropositive (general test)
- `_1_6_2_c_number_of_syphilis_positive_pregnanat_women_treated_for_syphilis` (FLOAT): Syphilis positive PW treated (general)
- `_1_7_1_number_of_pregnant_women_tested_positive_for_thyroid_disorder` (FLOAT): PW testing positive for thyroid disorder
- `_1_7_2_number_of_pregnant_women_treated_for_thyroid_disorder` (FLOAT): PW treated for thyroid disorder
- `_1_8_1_number_of_pregnant_women_screened_for_tb` (FLOAT): PW screened for TB
- `_1_8_2_number_of_pregnant_women_identified_with_presumptive_tb_symptoms` (FLOAT): PW identified with presumptive TB
- `_1_8_3_number_of_pregnant_women_referred_out_of_those_identified_with_presumptive_tb_symptoms` (FLOAT): PW with TB symptoms referred
- `_1_9_1_total_high_risk_pregnancy_hrp_intrapartum_including_following` (FLOAT): Total High Risk Pregnancy (HRP) intrapartum cases
- `_1_9_1_a_number_of_pregnant_women_with_post_partum_haemorrhage_immediately_after_delivery_in_the_facility` (FLOAT): PPH cases — older format
- `_1_9_1_a_number_of_pregnant_women_only_with_post_partum_haemorrhage_immediately_after_delivery_in_the_facility` (FLOAT): PPH cases — updated format
- `_1_9_1_b_number_of_pregnant_women_with_sepsis_in_the_facility` (FLOAT): Sepsis cases — older format
- `_1_9_1_b_number_of_pregnant_women_only_with_sepsis_in_the_facility` (FLOAT): Sepsis cases — updated format
- `_1_9_1_c_number_of_pregnant_women_identified_with_eclampsia_in_the_facility` (FLOAT): Eclampsia cases — older format
- `_1_9_1_c_number_of_pregnant_women_identified_only_with_eclampsia_in_the_facility` (FLOAT): Eclampsia cases — updated format
- `_1_9_1_d_number_of_pregnant_women_identified_with_obstructed_labour_in_the_facility` (FLOAT): Obstructed labour cases — older format
- `_1_9_1_d_number_of_pregnant_women_identified_only_with_obstructed_labour_in_the_facility` (FLOAT): Obstructed labour — updated format
- `_1_9_1_e_number_of_pregnant_women_identified_with_more_than_1_complication_listed_above` (FLOAT): PW with >1 complication
- `_1_9_2_total_high_risk_pregnancy_hrp_antepartum_only_new_cases_are_to_be_reported` (FLOAT): HRP antepartum new cases
- `_1_9_3_total_no_of_anc_or_pnc_cases_referred_to_higher_any_other_facility` (FLOAT): ANC/PNC cases referred out — older format
- `_1_9_3_total_no_of_highrisk_anc_cases_referred_to_higher_any_other_facility_referred_out` (FLOAT): High risk ANC referred out — updated format
- `_1_9_3_a_total_no_of_intrapartum_pnc_high_risk_pregnancy_cases_referred_to_higher_any_other_facility_referred_out` (FLOAT): Intrapartum/PNC HRP cases referred out
- `_1_9_4_total_no_of_anc_or_pnc_cases_referred_in_to_the_facility` (FLOAT): ANC/PNC cases referred in — older format
- `_1_9_4_total_no_of_highrisk_anc_cases_referred_in_to_the_facility_referred_in` (FLOAT): High risk ANC referred in — updated format
- `_1_9_4_a_total_no_of_intrapartum_pnc_high_risk_pregnancy_cases_attended_or_referred_into_the_facility_referred_in` (FLOAT): Intrapartum/PNC HRP cases referred in
- `_1_9_5_number_of_complicated_pregnancies_treated_with_blood_transfusion` (FLOAT): Complicated pregnancies treated with blood transfusion

### Module 2: Deliveries — Prefix: m2_ or _2_
- `m2_deliveries_2_1_1_a_number_of_home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm_midwife` (FLOAT): Home deliveries by SBA — older format
- `m2_deliveries_2_1_1_a_number_of_home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm` (FLOAT): Home deliveries by SBA — updated format
- `_2_1_1_b_number_of_home_deliveries_attended_by_non_sba_trained_birth_attendant_tba_relatives_etc` (FLOAT): Home deliveries by non-SBA — older format
- `_2_1_1_b_number_of_home_deliveries_attended_by_non_sba` (FLOAT): Home deliveries by non-SBA — updated format
- `_2_1_2_number_of_pw_given_tablet_misoprostol_during_home_delivery` (FLOAT): PW given misoprostol during home delivery
- `_2_1_3_number_of_newborns_received_7_home_based_newborn_care_hbnc_visits_in_case_of_home_delivery` (FLOAT): Newborns receiving 7 HBNC visits after home delivery
- `_2_2_number_of_institutional_deliveries_conducted_including_c_sections` (FLOAT): Total institutional deliveries including C-sections
- `_2_2_1_out_of_total_institutional_deliveries_number_of_women_discharged_within_48_hours_of_delivery` (FLOAT): Women discharged within 48 hours — older format
- `_2_2_1_out_of_total_institutional_deliveries_excluding_c_section_number_of_women_stayed_for_48_hours_or_more_after_delivery` (FLOAT): Women staying 48+ hours (excl C-section) — updated format
- `_2_2_2_number_of_newborns_received_6_hbnc_visits_after_institutional_delivery` (FLOAT): Newborns with 6 HBNC visits after institutional delivery — older format
- `_2_2_2_out_of_total_institutional_deliveries_number_of_institutional_deliveries_excluding_c_sections_conducted_at_night_8_pm_8_a` (FLOAT): Institutional deliveries at night (8PM–8AM) — updated format
- `_2_2_3_out_of_total_institutional_deliveries_number_of_institutional_deliveries_conducted_at_midwifery_led_care_unit_mlcu` (FLOAT): Deliveries at MLCU
- `_2_3_age_wise_total_number_of_delivery_home_institutional_reported_2_3_1_2_3_2_2_3_3_2_3_4` (FLOAT): Total deliveries (age-wise sum)
- `_2_3_1_out_of_total_number_of_delivery_pw_with_age_15_years` (FLOAT): Deliveries by PW aged <15 years — older format
- `_2_3_1_out_of_total_number_of_delivery_pw_with_age_less_than_15_years` (FLOAT): Deliveries by PW aged <15 years — updated format
- `_2_3_2_out_of_total_number_of_delivery_pw_with_age_15_19_years` (FLOAT): Deliveries by PW aged 15–19 years
- `_2_3_3_out_of_total_number_of_delivery_pw_with_age_19_49_years` (FLOAT): Deliveries by PW aged 19–49 years — older format
- `_2_3_3_out_of_total_number_of_delivery_pw_with_age_more_than_19_49_years` (FLOAT): Deliveries by PW aged 19–49 years — updated format
- `_2_3_4_out_of_total_number_of_delivery_pw_with_age_49_years` (FLOAT): Deliveries by PW aged >49 years — older format
- `_2_3_4_out_of_total_number_of_delivery_pw_with_age_more_than_49_years` (FLOAT): Deliveries by PW aged >49 years — updated format
- `_2_4_number_of_newborns_received_6_hbnc_visits_after_institutional_delivery` (FLOAT): Newborns with 6 HBNC visits — older format
- `_2_4_number_of_newborns_received_6_7_hbnc_visits_after_delivery_home_institutional` (FLOAT): Newborns with 6–7 HBNC visits (home + institutional) — updated format
- `_2_5_no_of_identified_sick_new_borns_referred_by_asha_to_facility_under_hbnc_programme` (FLOAT): Sick newborns referred by ASHA under HBNC
- `_2_6_total_number_of_children_received_all_scheduled_5_home_visits_under_hbyc` (FLOAT): Children receiving all 5 scheduled HBYC home visits

### Module 4: Pregnancy Outcomes & Newborn Care — Prefix: m4_ or _4_
- `m4_pregnancy_outcome_details_of_new_born_4_1_1_a_live_birth_male` (FLOAT): Live male births
- `_4_1_1_b_live_birth_female` (FLOAT): Live female births
- `_4_1_2_number_of_pre_term_newborns_37_weeks_of_pregnancy` (FLOAT): Pre-term newborns (<37 weeks)
- `_4_1_3_still_birth` (FLOAT): Total stillbirths — older format
- `_4_1_3_a_intrapartum_fresh_still_birth` (FLOAT): Fresh/intrapartum stillbirths — older format
- `_4_1_3_a_fresh_stillbirth_intrapartum_stillbirth_28_weeks_above` (FLOAT): Fresh stillbirths ≥28 weeks — updated format
- `_4_1_3_b_antepartum_macerated_still_birth` (FLOAT): Macerated/antepartum stillbirths — older format
- `_4_1_3_b_mascerated_stillbirth_antepartum_stillbirth_28_weeks_above` (FLOAT): Macerated stillbirths ≥28 weeks — updated format
- `_4_1_3_c_foetal_death_24_28_weeks` (FLOAT): Foetal deaths 24–28 weeks — older format
- `_4_1_3_c_foetal_death_24_27_completed_weeks` (FLOAT): Foetal deaths 24–27 completed weeks — updated format
- `_4_2_abortion_spontaneous` (FLOAT): Spontaneous abortions
- `_4_3_1_a_mtp_up_to_12_weeks_of_pregnancy` (FLOAT): MTPs surgical ≤12 weeks — older format
- `_4_3_1_a_surgical_mtps_upto_12_weeks_of_pregnancy` (FLOAT): MTPs surgical ≤12 weeks — updated format
- `_4_3_1_b_mtp_more_than_12_weeks_of_pregnancy` (FLOAT): MTPs surgical >12 weeks
- `_4_3_1_c_mtps_completed_through_medical_methods_of_abortion_mma` (FLOAT): MTPs via medical method (MMA)
- `_4_3_2_a_post_abortion_mtp_complications_identified` (FLOAT): Post-abortion complications identified — older format
- `_4_3_2_a_total_post_abortion_mtp_complications_identified` (FLOAT): Total post-abortion complications — updated format
- `_4_3_2_b_post_abortion_mtp_complications_treated` (FLOAT): Post-abortion complications treated — older format
- `_4_3_2_b_post_abortion_mtp_complications_identified_where_abortions_were_carried_out_in_facilities_other_than_public_and_accredi` (FLOAT): Complications from non-public/non-accredited facilities — updated format
- `_4_3_2_c_post_abortion_mtp_complications_treated` (FLOAT): Post-abortion complications treated — updated format
- `_4_3_3_number_of_women_provided_with_post_abortion_mtp_contraception` (FLOAT): Women given contraception post-abortion
- `_4_4_1_number_of_newborns_weighed_at_birth` (FLOAT): Newborns weighed at birth
- `_4_4_2_number_of_newborns_having_weight_less_than_2_5_kg` (FLOAT): Low birth weight newborns (<2.5 kg) — older format
- `_4_4_2_number_of_newborns_having_weight_less_than_2500_gms` (FLOAT): Low birth weight newborns (<2500g) — updated format
- `_4_4_2_a_out_of_the_above_number_of_newborns_having_weight_less_than_1800_gms` (FLOAT): Very low birth weight (<1800g)
- `_4_4_3_number_of_newborns_breast_fed_within_1_hour_of_birth` (FLOAT): Newborns breastfed within 1 hour of birth
- `_4_4_4_no_of_newborns_discharged_from_the_facility_were_exclusively_breastfed_till_discharge` (FLOAT): Newborns exclusively breastfed till discharge
- `_4_4_5_number_of_newborns_received_donor_human_milk_dhm_in_the_facility` (FLOAT): Newborns receiving Donor Human Milk (DHM)
- `_4_5_1_number_of_newborns_screened_for_defects_at_birth_as_per_comprehensive_newborn_screening_rbsk` (FLOAT): Newborns screened for birth defects (RBSK)
- `_4_5_1_a_number_of_newborns_identified_with_visible_birth_defects_including_neural_tube_defect_down_s_syndrome_cleft_lip_palate_` (FLOAT): Newborns with visible birth defects
- `_4_5_3_number_of_sncu_discharged_babies_screened_in_deic` (FLOAT): SNCU discharged babies screened in DEIC
- `_4_5_5_number_of_children_till_age_18_years_affected_with_selected_health_conditions_managed_for_4_ds_disease_deficiency_develop` (FLOAT): Children ≤18 years managed for 4Ds (Disease, Deficiency, Development, Disability)
- `_4_5_6_number_of_children_till_age_18_years_affected_with_selected_health_conditions_managed_by_intervention_surgical` (FLOAT): Children ≤18 years managed by surgical intervention
- `_4_5_7_number_of_children_till_age_18_years_managed_at_deic_district_early_intervention_centre` (FLOAT): Children managed at DEIC

### Module 9: Child Immunisation — Prefix: m9_ or _9_

**Birth doses (given at delivery):**
- `m9_child_immunisation_9_1_1_child_immunisation_vitamin_k_birth_dose` (FLOAT): Vitamin K birth dose
- `_9_1_2_child_immunisation_bcg` (FLOAT): BCG vaccine — best proxy for live births in immunisation system
- `_9_1_9_child_immunisation_opv_0_birth_dose` (FLOAT): OPV birth dose — older format
- `_9_1_6_child_immunisation_opv_0_birth_dose` (FLOAT): OPV birth dose — updated format
- `_9_1_13_child_immunisation_hepatitis_b0_birth_dose` (FLOAT): Hepatitis B birth dose — older format
- `_9_1_10_child_immunisation_hepatitis_b0_birth_dose` (FLOAT): Hepatitis B birth dose — updated format

**Primary series (6, 10, 14 weeks):**
- `_9_1_3_child_immunisation_dpt1` / `_9_1_4_child_immunisation_dpt2` / `_9_1_5_child_immunisation_dpt3` (FLOAT): DPT doses 1/2/3 — older format
- `_9_1_6_child_immunisation_pentavalent_1` / `_9_1_7_pentavalent_2` / `_9_1_8_pentavalent_3` (FLOAT): Pentavalent doses 1/2/3 — older format
- `_9_1_3_child_immunisation_pentavalent_1` / `_9_1_4_pentavalent_2` / `_9_1_5_pentavalent_3` (FLOAT): Pentavalent doses 1/2/3 — updated format
- `_9_1_10_child_immunisation_opv1` / `_9_1_11_opv2` / `_9_1_12_opv3` (FLOAT): OPV doses 1/2/3 — older format
- `_9_1_7_child_immunisation_opv1` / `_9_1_8_opv2` / `_9_1_9_opv3` (FLOAT): OPV doses 1/2/3 — updated format
- `_9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1` (FLOAT): IPV 1 — older format
- `_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1` (FLOAT): IPV 1 — updated format
- `_9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2` (FLOAT): IPV 2 — older format
- `_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2` (FLOAT): IPV 2 — updated format
- `_9_1_19_child_immunisation_rotavirus_1` / `_9_1_20_rotavirus_2` / `_9_1_21_rotavirus_3` (FLOAT): Rotavirus doses 1/2/3 — older format
- `_9_1_13_child_immunisation_rotavirus_1` / `_9_1_14_rotavirus_2` / `_9_1_15_rotavirus_3` (FLOAT): Rotavirus doses 1/2/3 — updated format
- `_9_1_22_child_immunisation_pcv_1` / `_9_1_23_pcv_2` (FLOAT): PCV doses 1/2 — older format
- `_9_1_16_child_immunisation_pcv1` / `_9_1_17_pcv2` (FLOAT): PCV doses 1/2 — updated format

**9–12 month doses:**
- `_9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose` (FLOAT): MR/MCV1 — older format
- `_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose` (FLOAT): MR/MCV1 — updated format
- `_9_2_2_child_immunisation_9_11months_measles_1st_dose` (FLOAT): Measles 1st dose (9–11 months) — older format
- `_9_2_3_child_immunisation_9_11months_je_1st_dose` (FLOAT): JE 1st dose (9–11 months)
- `_9_1_24_child_immunisation_pcv_booster` (FLOAT): PCV booster — older format
- `_9_2_4_child_immunisation_pcv_booster` (FLOAT): PCV booster — updated format
- `_9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3` (FLOAT): IPV 3 — older format
- `_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3` (FLOAT): IPV 3 — updated format

**Fully Immunized Children (FIC) — KEY METRIC:**
- `_9_2_4_a_children_aged_between_9_and_11_months_fully_immunized_male` (FLOAT): FIC male — older format
- `_9_2_4_b_children_aged_between_9_and_11_months_fully_immunized_female` (FLOAT): FIC female — older format
- `_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male` (FLOAT): FIC male — updated format
- `_9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female` (FLOAT): FIC female — updated format
- Total FIC = COALESCE(_9_2_5_a_..., _9_2_4_a_...) + COALESCE(_9_2_5_b_..., _9_2_4_b_...)

**Delayed vaccination (after 12 months):**
- `_9_3_1_child_immunisation_after_12_months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose` (FLOAT): Delayed MR/MCV1 — older format
- `_9_3_1_child_immunisation_after_12_months_delayed_vaccination_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose` (FLOAT): Delayed MR/MCV1 — updated format
- `_9_3_2_child_immunisation_after_12_months_je_1st_dose` (FLOAT): Delayed JE 1st dose — older format
- `_9_3_2_child_immunisation_after_12_months_delayed_vaccination_je_1st_dose` (FLOAT): Delayed JE 1st dose — updated format
- `_9_3_3_child_immunisation_dpt_1_after_12_months_of_age_delayed_vaccination` (FLOAT): Delayed DPT 1
- `_9_3_4_child_immunisation_dpt_2_after_12_months_of_age_delayed_vaccination` (FLOAT): Delayed DPT 2
- `_9_3_5_child_immunisation_dpt_3_after_12_months_of_age_delayed_vaccination` (FLOAT): Delayed DPT 3
- `_9_3_6_child_immunisation_dpt_booster_after_24_months_of_age_delayed_vaccination` (FLOAT): Delayed DPT booster
- `_9_3_7_child_immunisation_opv_booster_after_24_months_of_age_delayed_vaccination` (FLOAT): Delayed OPV booster
- `_9_3_8_child_immunisation_je_booster_after_24_months_of_age_delayed_vaccination` (FLOAT): Delayed JE booster

**16–24 month boosters:**
- `_9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months` (FLOAT): MR 2nd dose / MCV2 — older format
- `_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months` (FLOAT): MCV2 — updated format
- `_9_4_2_child_immunisation_measles_2nd_dose_more_than_16_months` (FLOAT): Measles 2nd dose (>16 months) — older format
- `_9_4_2_child_immunisation_dpt_1st_booster` (FLOAT): DPT 1st booster — updated format
- `_9_4_3_child_immunisation_dpt_1st_booster` (FLOAT): DPT 1st booster — older format
- `_9_4_3_child_immunisation_opv_booster` (FLOAT): OPV booster — updated format
- `_9_4_4_child_immunisation_opv_booster` (FLOAT): OPV booster — older format
- `_9_4_4_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine_2nd_dose_16_24_months` (FLOAT): JE 2nd dose — updated format
- `_9_4_5_child_immunisation_measles_mumps_rubella_mmr_vaccine` (FLOAT): MMR vaccine
- `_9_4_6_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine` (FLOAT): JE vaccine (>16 months) — older format

**Older children:**
- `_9_5_1_child_immunisation_typhoid` / `_9_5_1_child_immunization_typhoid` (FLOAT): Typhoid vaccine (note spelling variant)
- `_9_5_2_children_more_than_5_years_received_dpt5_2nd_booster` (FLOAT): DPT 2nd booster (>5 years)
- `_9_5_3_children_more_than_10_years_received_tt10_td10` (FLOAT): TT10/TD10 — older format
- `_9_5_3_children_more_than_10_years_received_td10_tetanus_diptheria10` (FLOAT): TD10 — updated format
- `_9_5_4_children_more_than_16_years_received_tt16_td16` (FLOAT): TT16/TD16 — older format
- `_9_5_4_children_more_than_16_years_received_td16_tetanus_diptheria16` (FLOAT): TD16 — updated format

**AEFI (Adverse Events Following Immunisation):**
- `_9_6_1_number_of_cases_of_aefi_abscess` (FLOAT): AEFI abscess — older format
- `_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc` (FLOAT): AEFI minor — updated format
- `_9_6_2_number_of_cases_of_aefi_death` (FLOAT): AEFI death — older format
- `_9_6_2_number_of_cases_of_aefi_severe_eg_anaphylaxis_fever_102_degrees_not_requiring_hospitalization_etc` (FLOAT): AEFI severe — updated format
- `_9_6_3_number_of_cases_of_aefi_others` (FLOAT): AEFI others — older format
- `_9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc` (FLOAT): AEFI serious — updated format
- `_9_6_3_a_out_of_number_of_cases_of_aefi_serious_total_number_of_aefi_deaths` (FLOAT): AEFI deaths (subset of serious)

**Session planning:**
- `_9_7_1_immunisation_sessions_planned` (FLOAT): Immunisation sessions planned
- `_9_7_2_immunisation_sessions_held` (FLOAT): Immunisation sessions actually held
- `_9_7_3_number_of_immunisation_sessions_where_ashas_were_present` (FLOAT): Sessions where ASHAs were present

**Vitamins, nutrition, deworming:**
- `_9_8_1_child_immunisation_vitamin_a_dose_1` (FLOAT): Vitamin A dose 1
- `_9_8_2_child_immunisation_vitamin_a_dose_5` (FLOAT): Vitamin A dose 5
- `_9_8_3_child_immunisation_vitamin_a_dose_9` (FLOAT): Vitamin A dose 9
- `_9_9_number_of_children_6_59_months_provided_8_10_doses_1ml_of_ifa_syrup_bi_weekly` (FLOAT): Children 6–59 months given IFA syrup
- `_9_10_number_of_children_12_59_months_provided_albendazole` (FLOAT): Children 12–59 months dewormed with albendazole
- `_9_11_number_of_severely_underweight_children_provided_health_checkup_0_5_years` (FLOAT): Severely underweight children (0–5 years) given health checkup

### Population Targets
- `facility_targets_block_population_202425` (FLOAT): Estimated block population for FY 2024–25 at facility level
- `facility_targets_estimated_infants_202425` (FLOAT): Estimated infants for FY 2024–25 at facility level (smallest unit)
- `block_targets_block_population_202425` (FLOAT): Estimated block population for FY 2024–25
- `block_targets_estimated_pregnancy_202425` (FLOAT): Estimated pregnancies for FY 2024–25 at block level
- `block_targets_estimated_infants_202425` (FLOAT): Estimated infants for FY 2024–25 at block level
- `district_targets_estimated_infants_202425` (FLOAT): Estimated infants for FY 2024–25 at district level
- `state_targets_estimated_infants_202425` (FLOAT): Estimated infants for FY 2024–25 at state level

---

## Important Notes for SQL Query Generation

### 1. Dual Format Columns (CRITICAL)
This table merges data from two HMIS format versions. Many indicators have two
columns covering the same metric — one from the older format, one from the newer.
A given row will have data in only one of the two (the other will be NULL).

BEFORE generating any SQL query, always run this discovery query first to fetch
the current full column list from BigQuery's INFORMATION_SCHEMA:

    SELECT column_name
    FROM `biharhmisdatafordvctool.BH_HMIS_Data`.INFORMATION_SCHEMA.COLUMNS
    WHERE table_name = 'BH_HMIS_Facility_Level_Data_with_all_Targets'
    ORDER BY ordinal_position

Then apply this logic to identify COALESCE pairs dynamically:
- Two columns are likely duplicates if they share the same core numeric prefix
  (e.g., _9_1_3_, _1_2_1_) but have different descriptive suffixes
- Two columns are likely duplicates if one name is a substring or close variant
  of the other (e.g., "pentavalent_1" appearing in both _9_1_3_ and _9_1_6_)
- When in doubt, check which column has non-null values for the rows in scope
  using: SELECT COUNT([col]) FROM ... WHERE [col] IS NOT NULL
- Always prefer the newer/longer column name as the first argument in COALESCE
  (it reflects the updated format), with the older name as fallback

The pairs listed below are known at time of writing — treat them as a reference,
not an exhaustive list. When new columns are added to the table, detect pairs
dynamically using the INFORMATION_SCHEMA approach above.

Known COALESCE pairs:
- FIC male:    COALESCE(_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male, _9_2_4_a_children_aged_between_9_and_11_months_fully_immunized_male)
- FIC female:  COALESCE(_9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female, _9_2_4_b_children_aged_between_9_and_11_months_fully_immunized_female)
- Pentavalent 1: COALESCE(_9_1_3_child_immunisation_pentavalent_1, _9_1_6_child_immunisation_pentavalent_1)
- Pentavalent 2: COALESCE(_9_1_4_child_immunisation_pentavalent_2, _9_1_7_child_immunisation_pentavalent_2)
- Pentavalent 3: COALESCE(_9_1_5_child_immunisation_pentavalent_3, _9_1_8_child_immunisation_pentavalent_3)
- OPV 0:  COALESCE(_9_1_6_child_immunisation_opv_0_birth_dose, _9_1_9_child_immunisation_opv_0_birth_dose)
- OPV 1:  COALESCE(_9_1_7_child_immunisation_opv1, _9_1_10_child_immunisation_opv1)
- OPV 2:  COALESCE(_9_1_8_child_immunisation_opv2, _9_1_11_child_immunisation_opv2)
- OPV 3:  COALESCE(_9_1_9_child_immunisation_opv3, _9_1_12_child_immunisation_opv3)
- HepB 0: COALESCE(_9_1_10_child_immunisation_hepatitis_b0_birth_dose, _9_1_13_child_immunisation_hepatitis_b0_birth_dose)
- IPV 1:  COALESCE(_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1, _9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1)
- IPV 2:  COALESCE(_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2, _9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2)
- IPV 3:  COALESCE(_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3, _9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3)
- Rotavirus 1: COALESCE(_9_1_13_child_immunisation_rotavirus_1, _9_1_19_child_immunisation_rotavirus_1)
- Rotavirus 2: COALESCE(_9_1_14_child_immunisation_rotavirus_2, _9_1_20_child_immunisation_rotavirus_2)
- Rotavirus 3: COALESCE(_9_1_15_child_immunisation_rotavirus_3, _9_1_21_child_immunisation_rotavirus_3)
- PCV 1:  COALESCE(_9_1_16_child_immunisation_pcv1, _9_1_22_child_immunisation_pcv_1)
- PCV 2:  COALESCE(_9_1_17_child_immunisation_pcv2, _9_1_23_child_immunisation_pcv_2)
- PCV booster: COALESCE(_9_2_4_child_immunisation_pcv_booster, _9_1_24_child_immunisation_pcv_booster)
- MR/MCV1 (9–12m): COALESCE(_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose, _9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose)
- DPT booster: COALESCE(_9_4_2_child_immunisation_dpt_1st_booster, _9_4_3_child_immunisation_dpt_1st_booster)
- OPV booster: COALESCE(_9_4_3_child_immunisation_opv_booster, _9_4_4_child_immunisation_opv_booster)
- JE 2nd dose: COALESCE(_9_4_4_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine_2nd_dose_16_24_months, _9_4_6_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine)
- Delayed MR/MCV1: COALESCE(_9_3_1_child_immunisation_after_12_months_delayed_vaccination_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose, _9_3_1_child_immunisation_after_12_months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose)
- Delayed JE: COALESCE(_9_3_2_child_immunisation_after_12_months_delayed_vaccination_je_1st_dose, _9_3_2_child_immunisation_after_12_months_je_1st_dose)
- AEFI minor:  COALESCE(_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc, _9_6_1_number_of_cases_of_aefi_abscess)
- AEFI severe: COALESCE(_9_6_2_number_of_cases_of_aefi_severe_eg_anaphylaxis_fever_102_degrees_not_requiring_hospitalization_etc, _9_6_2_number_of_cases_of_aefi_death)
- AEFI serious: COALESCE(_9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc, _9_6_3_number_of_cases_of_aefi_others)
- TD1: COALESCE(_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1, _1_2_1_number_of_pw_given_tt1_td1)
- TD2: COALESCE(_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2, _1_2_2_number_of_pw_given_tt2_td2)
- TD booster: COALESCE(_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster, _1_2_3_number_of_pw_given_tt_booster_td_booster)
- ANC registered: COALESCE(m1_ante_natal_care_anc_1_1_total_number_of_new_pregnant_women_registered_for_anc, m1_ante_natal_care_anc_1_1_total_number_of_pregnant_women_registered_for_anc)
- 1st trimester ANC: COALESCE(_1_1_1_out_of_the_total_new_anc_registered_number_registered_within_1st_trimester_within_12_weeks, _1_1_1_out_of_the_total_anc_registered_number_registered_within_1st_trimester_within_12_weeks)
- Anaemia mild-moderate: COALESCE(_1_4_2_number_of_pw_having_hb_level_11_7_1_to_10_9_g_dl_out_of_total_tested_cases, _1_4_2_number_of_pw_having_hb_level_11_out_of_total_tested_cases_7_1_to_10_9)
- Anaemia severe: COALESCE(_1_4_3_number_of_pw_having_hb_level_7_g_dl_out_of_total_tested_cases, _1_4_3_number_of_pw_having_hb_level_7_out_of_total_tested_cases)
- Severe anaemia treated: COALESCE(_1_4_4_number_of_pw_treated_for_severe_anaemia_hb_7g_dl_out_of_total_tested_cases, _1_4_4_number_of_pw_having_severe_anaemia_hb_7_treated)
- Stillbirth fresh: COALESCE(_4_1_3_a_fresh_stillbirth_intrapartum_stillbirth_28_weeks_above, _4_1_3_a_intrapartum_fresh_still_birth)
- Stillbirth macerated: COALESCE(_4_1_3_b_mascerated_stillbirth_antepartum_stillbirth_28_weeks_above, _4_1_3_b_antepartum_macerated_still_birth)
- Surgical MTP ≤12 weeks: COALESCE(_4_3_1_a_surgical_mtps_upto_12_weeks_of_pregnancy, _4_3_1_a_mtp_up_to_12_weeks_of_pregnancy)
- Post-abortion complications treated: COALESCE(_4_3_2_c_post_abortion_mtp_complications_treated, _4_3_2_b_post_abortion_mtp_complications_treated)
- Home deliveries SBA: COALESCE(m2_deliveries_2_1_1_a_number_of_home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm, m2_deliveries_2_1_1_a_number_of_home_deliveries_attended_by_skill_birth_attendant_sba_doctor_nurse_anm_midwife)
- Non-SBA home deliveries: COALESCE(_2_1_1_b_number_of_home_deliveries_attended_by_non_sba, _2_1_1_b_number_of_home_deliveries_attended_by_non_sba_trained_birth_attendant_tba_relatives_etc)
- HBNC visits after delivery: COALESCE(_2_4_number_of_newborns_received_6_7_hbnc_visits_after_delivery_home_institutional, _2_4_number_of_newborns_received_6_hbnc_visits_after_institutional_delivery)
- Low birth weight: COALESCE(_4_4_2_number_of_newborns_having_weight_less_than_2500_gms, _4_4_2_number_of_newborns_having_weight_less_than_2_5_kg)

### 2. Coverage Calculation Formula (CRITICAL)
Coverage (%) = (SUM of numerator column(s) for the period / SUM of target column for the period) × 100

- Default denominator: estimated infants (unless indicator-specific — see below)
- ANC coverage uses: block_targets_estimated_pregnancy_202425 as denominator
- For immunisation coverage: numerator = relevant vaccine dose count (using COALESCE pairs)
- For FIC coverage: numerator = total FIC male + FIC female (both COALESCEd)
- NEVER divide row-by-row then average — always SUM numerator / SUM denominator first
- Multiply by 100 to express as percentage
- Round to 1 decimal place in final output

### 2.1 "Average over a range of months" — definition (CRITICAL)
This rule applies ONLY when the user explicitly uses the word "average" or
"avg" in a query whose period spans more than one month — for any kind of
metric (coverage %, rate, completeness %, correctness %, PRE %, etc.).

Definition: compute the metric separately for EACH individual month using
its normal single-month formula, then take the simple AVG across those
per-month values.

Example: "average % facilities 100% complete in Patna from Jan to Mar 2026"
→ compute % facilities fully complete for January 2026 (one number)
→ compute it for February 2026 (another number)
→ compute it for March 2026 (another number)
→ AVG those three values

When the user does NOT say "average" / "avg", do NOT use this — keep the
default Rule 2 behavior (SUM numerator / SUM denominator over the entire
period, single answer).

SQL pattern — produce one row per month in a CTE, then AVG those:

```sql
WITH monthly AS (
  SELECT Month,
    <single-month metric expression, producing one number per month>
  FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
  WHERE infacility_or_outreach_or_total = 'Total'
    AND {geo_filter}
    AND Month BETWEEN '<start>' AND '<end>'
  GROUP BY Month
)
SELECT
  COUNT(*) AS months_in_range,
  ROUND(AVG(<per-month metric>), 1) AS avg_metric
FROM monthly
```

For single-month queries that include "average", the result is identical
to the single-month value — just compute it normally.

### 3. Which Target Column to Use Based on Query Level (CRITICAL)
Match the target column to the geographic scope of the question being asked —
NOT the level being filtered to.

| Query is about...       | Infant target column to use                        |
|-------------------------|----------------------------------------------------|
| A specific facility     | facility_targets_estimated_infants_202425          |
| A block / set of blocks | block_targets_estimated_infants_202425             |
| A district              | district_targets_estimated_infants_202425          |
| A state                 | state_targets_estimated_infants_202425             |

For ANC/pregnancy indicators:
| Block level             | block_targets_estimated_pregnancy_202425           |

When aggregating multiple facilities to get block/district/state coverage,
use the pre-defined block/district/state target — do NOT sum facility-level
targets, as this leads to double-counting.

### 4. Standard Filters to Always Apply
- Always filter: `infacility_or_outreach_or_total = 'Total'` unless user asks
  for in-facility or outreach breakdown specifically
- Month is STRING in YYYY-MM-DD format — use: `Month >= '2024-04-01'`
- Indian financial year runs April to March (FY 2024–25 = April 2024 to March 2025)
- When user says "this year" or "current year", assume current financial year

### 5. BCG as Births Proxy
`_9_1_2_child_immunisation_bcg` is the best proxy for live births registered
in the immunisation system when birth registration data is unavailable.

### 6. Session Performance
- Session dropout rate = (Sessions Planned − Sessions Held) / Sessions Planned × 100
- ASHA presence rate = Sessions with ASHA / Sessions Held × 100
- Columns: `_9_7_1_immunisation_sessions_planned`, `_9_7_2_immunisation_sessions_held`,
  `_9_7_3_number_of_immunisation_sessions_where_ashas_were_present`

### 7. Geographic Name Matching (CRITICAL)
User-typed names of states, districts, blocks, or facilities will often NOT exactly
match the values stored in BigQuery. The agent must handle this intelligently.

**Step 1 — Always use case-insensitive, fuzzy matching in SQL:**
Never use exact equality for geographic names. Always use LIKE with LOWER():
    LOWER(district_name) LIKE LOWER('%user_input%')
    LOWER(sub_district_ulb_name) LIKE '%sadar%'   ← use this for blocks (NOT block_name)

**Step 2 — Before running the main query, run a name lookup first:**
When a user mentions a geographic name, first run a discovery query to find
what actually exists in the table:

    -- For district lookup
    SELECT DISTINCT district_name, district_code
    FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
    WHERE LOWER(district_name) LIKE LOWER('%user_input%')
    LIMIT 20

    -- For block lookup (use sub_district_ulb columns — block_name/block_code have data anomalies)
    SELECT DISTINCT sub_district_ulb_name, sub_district_ulb_code, district_name
    FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
    WHERE LOWER(sub_district_ulb_name) LIKE LOWER('%user_input%')
    LIMIT 20

    -- For facility lookup (always include sub_district_ulb and district)
    SELECT DISTINCT facility_name, facility_code, sub_district_ulb_name, district_name
    FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
    WHERE LOWER(facility_name) LIKE LOWER('%user_input%')
    LIMIT 20

**Step 3 — Handle the result of the lookup:**

| Lookup result                                          | Action                                                                                      |
|--------------------------------------------------------|---------------------------------------------------------------------------------------------|
| Exactly 1 match found                                  | Proceed with main query using the exact name/code from the table                            |
| 0 matches found                                        | Tell the user no match was found, ask them to check the spelling                            |
| 2+ matches with same name, different district/block    | Ask user to clarify — list all options with their district/block context                    |
| 2+ matches with clearly different names (partial hits) | Pick the closest match, state the assumption, and invite correction                         |

Example clarification message to user:
"I found multiple blocks matching 'Sadar' in Bihar — which one did you mean?
1. Sadar — Patna district (sub_district_ulb_code: XXXX)
2. Sadar — Gaya district (sub_district_ulb_code: XXXX)
3. Sadar — Muzaffarpur district (sub_district_ulb_code: XXXX)
Please reply with the number or district name."

**Step 4 — Always use numeric code in the final query, not name string:**
Once the correct entity is confirmed, use its numeric code in the WHERE clause —
not the name. This avoids all residual spacing, casing, and encoding issues:
    WHERE district_code = 191              -- not WHERE district_name = 'Begusarai'
    WHERE sub_district_ulb_code = 1671     -- not WHERE sub_district_ulb_name = 'Sadar'
    WHERE facility_code = 304569           -- not WHERE facility_name = 'PHC Samho'
NEVER use block_code or block_name in any WHERE clause — those columns have data anomalies.

**Common name issues to anticipate in Bihar HMIS data:**
- Case differences: "begusarai" vs "Begusarai" vs "BEGUSARAI"
- Trailing or leading spaces in stored values
- Hindi transliteration variants: "Muzaffarpur" vs "Muzzaffarpur"
- Same block name in multiple districts — extremely common in Bihar
  (e.g., "Sadar" block exists in almost every district)
- Abbreviated vs full facility names: "PHC Samho" vs "Primary Health Centre Samho"
- Format variants: "AAM-PHC" vs "Aam PHC" vs "AAMPHC"
- Sub Centre names often duplicated across blocks

### 8. Never Run Write Operations
Only SELECT queries are permitted under any circumstances.
Never generate DELETE, UPDATE, INSERT, DROP, TRUNCATE, or CREATE statements.
If asked to modify data, decline and explain that this bot is read-only.

---

## Domain Glossary
- PRE: Probable Reporting Error — a discrepancy between co-administered antigens (should be equal) or a logically impossible value. N=16 checks; see PRE section below.
- Co-admin: vaccines given at the same visit that should always have the same count
- ZD / Zero Dose: Infants who did NOT receive Pentavalent 1 (the standard "first
  vaccine" proxy). Calculated as: ZD count = Target − Penta1 doses given.
  ZD rate (%) = ZD count ÷ Target × 100  (equivalent to 100% − Penta1 coverage %).
- FIC: Fully Immunized Children — completed all vaccines in primary schedule by 9–12 months
- RI: Routine Immunization
- HMIS: Health Management Information System
- ANC: Ante-Natal Care
- PW: Pregnant Women
- PNC: Post-Natal Care
- HBNC: Home Based Newborn Care
- HBYC: Home Based Young Child Care
- SBA: Skilled Birth Attendant
- ASHA: Accredited Social Health Activist (community health worker)
- ANM: Auxiliary Nurse Midwife
- RBSK: Rashtriya Bal Swasthya Karyakram (newborn and child health screening)
- DEIC: District Early Intervention Centre
- SNCU: Special Newborn Care Unit
- MTP: Medical Termination of Pregnancy
- GDM: Gestational Diabetes Mellitus
- HRP: High Risk Pregnancy
- PPH: Post-Partum Haemorrhage
- AEFI: Adverse Events Following Immunisation
- IFA: Iron Folic Acid
- IPV: Inactivated (Injectable) Polio Vaccine
- OPV: Oral Polio Vaccine
- BCG: Bacillus Calmette-Guérin (TB vaccine given at birth)
- DPT: Diphtheria, Pertussis, Tetanus vaccine
- MR: Measles-Rubella vaccine
- MCV: Measles Containing Vaccine
- JE: Japanese Encephalitis vaccine
- PCV: Pneumococcal Conjugate Vaccine
- UP: Uttar Pradesh
- MPV: Mission Parivar Vikas (high fertility district program)
- PHC: Primary Health Centre
- AAM-PHC: Ayushman Arogya Mandir Primary Health Centre
- Sub Centre: Lowest level health facility
- MLCU: Midwifery Led Care Unit
- DHM: Donor Human Milk
- OGTT: Oral Glucose Tolerance Test
- FY: Financial Year (April to March in India)

---

## Data Completeness Checking

### What "completeness" means
A facility-month record is **complete for a variable** when at least one of its source columns is NOT NULL.
A record is **fully complete** when all 50 variables below are non-null.

### Critical: use IS NOT NULL, never COALESCE for completeness
Some column pairs have mismatched data types (FLOAT64 and STRING). `COALESCE(float_col, string_col)` causes a BigQuery type error.
Always check nulls with:
- Single column: `col IS NOT NULL`
- COALESCE pair: `(col_a IS NOT NULL OR col_b IS NOT NULL)`

Then wrap in `IF(..., 1, 0)` to count them.

### The 50 completeness variables (4 sections)

**Section A — ANC, Deliveries & Live Births (6 fields)**
| Variable | Check |
|----------|-------|
| TD1 | `(_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1 IS NOT NULL OR _1_2_1_number_of_pw_given_tt1_td1 IS NOT NULL)` |
| TD2 | `(_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2 IS NOT NULL OR _1_2_2_number_of_pw_given_tt2_td2 IS NOT NULL)` |
| TD Booster | `(_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster IS NOT NULL OR _1_2_3_number_of_pw_given_tt_booster_td_booster IS NOT NULL)` |
| Inst. Deliveries | `_2_2_number_of_institutional_deliveries_conducted_including_c_sections IS NOT NULL` |
| Live Births Male | `m4_pregnancy_outcome_details_of_new_born_4_1_1_a_live_birth_male IS NOT NULL` |
| Live Births Female | `_4_1_1_b_live_birth_female IS NOT NULL` |

**Section B1 — Birth to 1-Year Doses (23 fields)**
| Variable | Check |
|----------|-------|
| HepB0 | `(_9_1_10_child_immunisation_hepatitis_b0_birth_dose IS NOT NULL OR _9_1_13_child_immunisation_hepatitis_b0_birth_dose IS NOT NULL)` |
| OPV0 | `(_9_1_6_child_immunisation_opv_0_birth_dose IS NOT NULL OR _9_1_9_child_immunisation_opv_0_birth_dose IS NOT NULL)` |
| BCG | `_9_1_2_child_immunisation_bcg IS NOT NULL` |
| Penta1 | `(_9_1_3_child_immunisation_pentavalent_1 IS NOT NULL OR _9_1_6_child_immunisation_pentavalent_1 IS NOT NULL)` |
| OPV1 | `(_9_1_7_child_immunisation_opv1 IS NOT NULL OR _9_1_10_child_immunisation_opv1 IS NOT NULL)` |
| IPV1 | `(_9_1_11_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1 IS NOT NULL OR _9_1_17_child_immunisation_inactivated_injectable_polio_vaccine_1_ipv_1 IS NOT NULL)` |
| RVV1 | `(_9_1_13_child_immunisation_rotavirus_1 IS NOT NULL OR _9_1_19_child_immunisation_rotavirus_1 IS NOT NULL)` |
| PCV1 | `(_9_1_16_child_immunisation_pcv1 IS NOT NULL OR _9_1_22_child_immunisation_pcv_1 IS NOT NULL)` |
| Penta2 | `(_9_1_4_child_immunisation_pentavalent_2 IS NOT NULL OR _9_1_7_child_immunisation_pentavalent_2 IS NOT NULL)` |
| OPV2 | `(_9_1_8_child_immunisation_opv2 IS NOT NULL OR _9_1_11_child_immunisation_opv2 IS NOT NULL)` |
| RVV2 | `(_9_1_14_child_immunisation_rotavirus_2 IS NOT NULL OR _9_1_20_child_immunisation_rotavirus_2 IS NOT NULL)` |
| Penta3 | `(_9_1_5_child_immunisation_pentavalent_3 IS NOT NULL OR _9_1_8_child_immunisation_pentavalent_3 IS NOT NULL)` |
| OPV3 | `(_9_1_9_child_immunisation_opv3 IS NOT NULL OR _9_1_12_child_immunisation_opv3 IS NOT NULL)` |
| IPV2 | `(_9_1_12_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2 IS NOT NULL OR _9_1_18_child_immunisation_inactivated_injectable_polio_vaccine_2_ipv_2 IS NOT NULL)` |
| RVV3 | `(_9_1_15_child_immunisation_rotavirus_3 IS NOT NULL OR _9_1_21_child_immunisation_rotavirus_3 IS NOT NULL)` |
| PCV2 | `(_9_1_17_child_immunisation_pcv2 IS NOT NULL OR _9_1_23_child_immunisation_pcv_2 IS NOT NULL)` |
| MR1 (9m) | `(_9_2_2_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL OR _9_2_1_child_immunisation_9_11months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL)` |
| IPV3 | `(_9_2_1_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3 IS NOT NULL OR _9_2_5_child_immunisation_9_11_months_inactivated_injectable_polio_vaccine_3_ipv_3 IS NOT NULL)` |
| PCV Booster | `(_9_2_4_child_immunisation_pcv_booster IS NOT NULL OR _9_1_24_child_immunisation_pcv_booster IS NOT NULL)` |
| FIC Male | `(_9_2_5_a_fully_immunized_children_aged_between_9_and_12_months_male IS NOT NULL OR _9_2_4_a_children_aged_between_9_and_11_months_fully_immunized_male IS NOT NULL)` |
| FIC Female | `(_9_2_5_b_fully_immunized_children_aged_between_9_and_12_months_female IS NOT NULL OR _9_2_4_b_children_aged_between_9_and_11_months_fully_immunized_female IS NOT NULL)` |
| Vitamin A1 | `_9_8_1_child_immunisation_vitamin_a_dose_1 IS NOT NULL` |
| JE1 (9m) | `_9_2_3_child_immunisation_9_11months_je_1st_dose IS NOT NULL` |

**Section B2 — 1-Year+ & Booster Doses (15 fields)**
| Variable | Check |
|----------|-------|
| Delayed MR1 | `(_9_3_1_child_immunisation_after_12_months_delayed_vaccination_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL OR _9_3_1_child_immunisation_after_12_months_measles_rubella_mr_measles_containing_vaccine_mcv_1st_dose IS NOT NULL)` |
| DPT1 (delayed) | `_9_3_3_child_immunisation_dpt_1_after_12_months_of_age_delayed_vaccination IS NOT NULL` |
| DPT2 (delayed) | `_9_3_4_child_immunisation_dpt_2_after_12_months_of_age_delayed_vaccination IS NOT NULL` |
| DPT3 (delayed) | `_9_3_5_child_immunisation_dpt_3_after_12_months_of_age_delayed_vaccination IS NOT NULL` |
| DPT Booster (2yr) | `_9_3_6_child_immunisation_dpt_booster_after_24_months_of_age_delayed_vaccination IS NOT NULL` |
| OPV Booster (2yr) | `_9_3_7_child_immunisation_opv_booster_after_24_months_of_age_delayed_vaccination IS NOT NULL` |
| MR2 (16m) | `(_9_4_1_child_immunisation_measles_rubella_mr_measles_containing_vaccine_mcv_2nd_dose_16_24_months IS NOT NULL OR _9_4_1_child_immunisation_measles_rubella_mr_2nd_dose_16_24_months IS NOT NULL)` |
| DPT Booster 1 | `(_9_4_2_child_immunisation_dpt_1st_booster IS NOT NULL OR _9_4_3_child_immunisation_dpt_1st_booster IS NOT NULL)` |
| OPV Booster | `(_9_4_3_child_immunisation_opv_booster IS NOT NULL OR _9_4_4_child_immunisation_opv_booster IS NOT NULL)` |
| DPT Booster 2 (5yr) | `_9_5_2_children_more_than_5_years_received_dpt5_2nd_booster IS NOT NULL` |
| TD10 | `(_9_5_3_children_more_than_10_years_received_td10_tetanus_diptheria10 IS NOT NULL OR _9_5_3_children_more_than_10_years_received_tt10_td10 IS NOT NULL)` |
| TD16 | `(_9_5_4_children_more_than_16_years_received_td16_tetanus_diptheria16 IS NOT NULL OR _9_5_4_children_more_than_16_years_received_tt16_td16 IS NOT NULL)` |
| JE1 (delayed 1yr) | `(_9_3_2_child_immunisation_after_12_months_delayed_vaccination_je_1st_dose IS NOT NULL OR _9_3_3_child_immunisation_after_12_months_je_1st_dose IS NOT NULL)` |
| JE Booster (2yr) | `_9_3_8_child_immunisation_je_booster_after_24_months_of_age_delayed_vaccination IS NOT NULL` |
| JE2 (16m) | `(_9_4_4_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine_2nd_dose_16_24_months IS NOT NULL OR _9_4_6_number_of_children_more_than_16_months_of_age_who_received_japanese_encephalitis_je_vaccine IS NOT NULL)` |

**Section C — AEFI & Sessions (6 fields)**
| Variable | Check |
|----------|-------|
| AEFI Minor | `(_9_6_1_number_of_cases_of_aefi_minor_eg_fever_rash_pain_etc IS NOT NULL OR _9_6_1_number_of_cases_of_aefi_abscess IS NOT NULL)` |
| AEFI Severe | `(_9_6_2_number_of_cases_of_aefi_severe_eg_anaphylaxis_fever_102_degrees_not_requiring_hospitalization_etc IS NOT NULL OR _9_6_2_number_of_cases_of_aefi_death IS NOT NULL)` |
| AEFI Serious | `(_9_6_3_number_of_cases_of_aefi_serious_eg_hospitalization_death_disability_cluster_etc IS NOT NULL OR _9_6_3_number_of_cases_of_aefi_others IS NOT NULL)` |
| AEFI Deaths | `_9_6_3_a_out_of_number_of_cases_of_aefi_serious_total_number_of_aefi_deaths IS NOT NULL` |
| Sessions Planned | `_9_7_1_immunisation_sessions_planned IS NOT NULL` |
| Sessions Held | `_9_7_2_immunisation_sessions_held IS NOT NULL` |

### SQL pattern for completeness queries

Always use this two-CTE structure:

```sql
WITH agg AS (
  SELECT
    Month, facility_name, facility_code,
    -- Section A (6 fields)
    (
      IF((_1_2_1_number_of_pw_given_td1_tetanus_diptheria_dose_1 IS NOT NULL OR _1_2_1_number_of_pw_given_tt1_td1 IS NOT NULL), 1, 0) +
      IF((_1_2_2_number_of_pw_given_td2_tetanus_diptheria_dose_2 IS NOT NULL OR _1_2_2_number_of_pw_given_tt2_td2 IS NOT NULL), 1, 0) +
      IF((_1_2_3_number_of_pw_given_td_booster_tetanus_diptheria_dose_booster IS NOT NULL OR _1_2_3_number_of_pw_given_tt_booster_td_booster IS NOT NULL), 1, 0) +
      IF(_2_2_number_of_institutional_deliveries_conducted_including_c_sections IS NOT NULL, 1, 0) +
      IF(m4_pregnancy_outcome_details_of_new_born_4_1_1_a_live_birth_male IS NOT NULL, 1, 0) +
      IF(_4_1_1_b_live_birth_female IS NOT NULL, 1, 0)
    ) AS a_sum,
    -- Section B1 (23 fields) — list all 23 IF expressions
    (...) AS b1_sum,
    -- Section B2 (15 fields)
    (...) AS b2_sum,
    -- Section C (6 fields)
    (...) AS c_sum
  FROM `biharhmisdatafordvctool.BH_HMIS_Data.BH_HMIS_Facility_Level_Data_with_all_Targets`
  WHERE infacility_or_outreach_or_total = 'Total'
    AND {geo_filter}
    AND {month_filter}
),
totals AS (
  SELECT *, a_sum + b1_sum + b2_sum + c_sum AS total_sum
  FROM agg
)
SELECT
  {optional_group_cols}       -- e.g. Month for trend, facility_name for per-facility
  COUNT(*) AS facility_month_records,
  -- Fully complete: all 50 fields filled
  COUNTIF(total_sum = 50)                                  AS fully_complete,
  ROUND(COUNTIF(total_sum = 50) * 100.0 / COUNT(*), 1)    AS pct_fully_complete,
  -- Per-section: % facilities where ALL fields in that section are filled
  COUNTIF(a_sum  = 6)                                      AS section_a_fully_complete,
  ROUND(COUNTIF(a_sum  = 6)  * 100.0 / COUNT(*), 1)       AS section_a_pct_fully,
  COUNTIF(b1_sum = 23)                                     AS section_b1_fully_complete,
  ROUND(COUNTIF(b1_sum = 23) * 100.0 / COUNT(*), 1)       AS section_b1_pct_fully,
  COUNTIF(b2_sum = 15)                                     AS section_b2_fully_complete,
  ROUND(COUNTIF(b2_sum = 15) * 100.0 / COUNT(*), 1)       AS section_b2_pct_fully,
  COUNTIF(c_sum  = 6)                                      AS section_c_fully_complete,
  ROUND(COUNTIF(c_sum  = 6)  * 100.0 / COUNT(*), 1)       AS section_c_pct_fully
FROM totals
{optional_group_by}
{optional_order_by}
LIMIT 100
```

### Grouping for completeness queries
- **Single period summary** (e.g. "completeness in Arwal in Dec 2025"): no GROUP BY — one aggregate row
- **Trend / month-on-month** (e.g. "completeness over last 6 months"): `GROUP BY Month ORDER BY Month`
- **Per-facility breakdown** (e.g. "which facilities are incomplete"): `GROUP BY facility_name, facility_code ORDER BY pct_fully_complete`
- **Per-block or per-district** (e.g. "completeness by block in Patna"): `GROUP BY sub_district_ulb_name, sub_district_ulb_code ORDER BY pct_fully_complete`

### How to summarize completeness results
Always report **% facilities fully complete** (not field-level averages), broken down by section:
- "X out of Y facility-month records (Z%) have all 50 fields complete"
- Then per section: "Section A (ANC/Deliveries): X out of Y (Z%)"
The section breakdown helps identify which reporting area has the most gaps.

---

## Probable Reporting Errors (PRE) Checking

### What PREs are
Co-administered vaccines (given at the same session / visit) should have identical counts.
When they don't, it signals a probable reporting error — the reporter entered different
numbers for antigens that should be the same. There are also three logical-impossibility
checks. In total, N = 16 PRE checks across 5 groups.

### The 16 PRE checks

**Group A1 — Penta1 vs co-admin antigens (6 weeks, 4 checks)**
| Check | Condition flagged as PRE |
|-------|--------------------------|
| A1a | Penta1 ≠ OPV1 |
| A1b | Penta1 ≠ IPV1 |
| A1c | Penta1 ≠ RVV1 (Rotavirus 1) |
| A1d | Penta1 ≠ PCV1 |

**Group A2 — Penta2 vs co-admin antigens (10 weeks, 2 checks)**
| Check | Condition flagged as PRE |
|-------|--------------------------|
| A2a | Penta2 ≠ OPV2 |
| A2b | Penta2 ≠ RVV2 (Rotavirus 2) |

**Group A3 — Penta3 vs co-admin antigens (14 weeks, 4 checks)**
| Check | Condition flagged as PRE |
|-------|--------------------------|
| A3a | Penta3 ≠ OPV3 |
| A3b | Penta3 ≠ IPV2 |
| A3c | Penta3 ≠ RVV3 (Rotavirus 3) |
| A3d | Penta3 ≠ PCV2 |

**Group A4 — MR1 vs co-admin antigens (9–11 months, 3 checks)**
| Check | Condition flagged as PRE |
|-------|--------------------------|
| A4a | MR1(9-11m) ≠ IPV3 |
| A4b | MR1(9-11m) ≠ PCV-Booster |
| A4c | MR1(9-11m) ≠ JE1(9-11m) |

**Group B — Logical impossibilities / suspicious patterns (3 checks)**
| Check | Condition flagged as PRE | Exact SQL |
|-------|--------------------------|-----------|
| B1 | Penta3 > Penta1 | `IF(v_penta3 > v_penta1, 1, 0)` |
| B2 | MR2 > MR1 | `IF(v_mr2 > v_mr1, 1, 0)` |
| B3 | Minor AEFI = 0 | `IF(v_aefi_minor = 0, 1, 0)` |

**CRITICAL — B3 exact SQL**: `IF(v_aefi_minor = 0, 1, 0) AS pre_b3`
- Do NOT add any condition involving `v_sess_held` or sessions to B3.
- Do NOT add `v_sess_held > 0`, `v_sess_held IS NOT NULL`, or any sessions guard.
- Do NOT use COALESCE on v_aefi_minor for B3.
- The only variable that matters for B3 is `v_aefi_minor`. If it equals 0, fire. Period.

### PRE firing rule
All 16 checks fire unconditionally — no IS NOT NULL guards. SQL NULL-comparison
semantics handle missing data naturally: `NULL != anything` and `NULL > anything`
both return NULL, which BigQuery's IF() treats as falsy (returns 0). So a check
only fires when both operands are present and the condition is actually met.

### PRE primary metric
`pct_pre` = `ROUND(AVG(pre_total) * 100.0 / 16, 1)`
where `pre_total` = number of the 16 checks that fired for that facility-month record.
This is the average fraction of PRE categories violated across all records in scope.

### SQL structure for PRE queries — always use 3 CTEs

```sql
WITH indicators AS (
  SELECT Month, facility_name, facility_code,
    COALESCE(new_penta1_col, old_penta1_col) AS v_penta1,
    ... (all 20 indicators defined as aliases)
  FROM `...table...`
  WHERE infacility_or_outreach_or_total = 'Total'
    AND {geo_filter} AND {month_filter}
),
pre_flags AS (
  SELECT *,
    IF(v_penta1 IS NOT NULL AND v_opv1 IS NOT NULL AND (v_penta1 != v_opv1), 1, 0) AS pre_a1a,
    ... (all 16 checks)
  FROM indicators
),
totals AS (
  SELECT *, pre_a1a + pre_a1b + ... + pre_b3 AS pre_total
  FROM pre_flags
)
SELECT
  COUNT(*) AS facility_month_records,
  ROUND(AVG(pre_total) * 100.0 / 16, 1) AS pct_pre,
  COUNTIF(pre_total = 0) AS facilities_zero_pre,
  ROUND(COUNTIF(pre_total = 0) * 100.0 / COUNT(*), 1) AS pct_facilities_zero_pre,
  -- group rates and individual check rates...
FROM totals
```

CRITICAL: Some old-format columns are stored as STRING in BigQuery, not FLOAT64.
Always wrap every column in SAFE_CAST(... AS FLOAT64) inside the indicators CTE.
`COALESCE(SAFE_CAST(col_a AS FLOAT64), SAFE_CAST(col_b AS FLOAT64))` — never raw COALESCE.

### Grouping for PRE queries
- Single period summary → no GROUP BY
- Trend / month-on-month → GROUP BY Month ORDER BY Month
- Per-facility → GROUP BY facility_name, facility_code ORDER BY pct_pre DESC
- Per-block → GROUP BY sub_district_ulb_name, sub_district_ulb_code ORDER BY pct_pre DESC

### How to report PRE results
- Lead with overall `pct_pre` (lower is better; > 5% = concern, > 15% = high)
- Show % facilities with zero PREs
- Show group-level error rates (A1 through B)
- Flag groups > 5% as "needs attention"
- Clarify: PREs indicate reporting discrepancies, not necessarily real-world mistakes

---

## Response Guidelines for the Bot
- Always respond in simple, clear language — avoid technical jargon
- Lead with the direct answer, then provide supporting numbers
- When showing coverage, always mention the numerator and denominator alongside the percentage
- If data is missing or null for the period, say so clearly — do not assume zero
- Keep responses under 300 words for WhatsApp readability
- If the user writes in Hindi, respond in Hindi
- If a query is ambiguous (e.g., "show BCG data" without a time or place filter),
  ask one clarifying question before generating SQL
- Round all percentages to 1 decimal place
- For large numbers, use Indian number formatting (e.g., 1,00,000 not 100,000)
