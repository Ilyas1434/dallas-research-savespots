#!/usr/bin/env python
"""
OD2A module -- Dallas County overdose surveillance pipeline.

Extracts REAL OBSERVED PUBLISHED numbers (no estimates, no extrapolation)
from the county's own surveillance report into a citable data layer.

Source of truth (single document):
  DCHHS "Overdose Data to Action (OD2A): 2024 Annual Surveillance Report of
  Preliminary Trends in Drug Overdoses in Dallas County"
  (Dallas County Health & Human Services). 20-page PDF.

What this script does
  1. Downloads the OD2A PDF to data/raw/od2a/ (+ a dated snapshot), reusing an
     existing copy if present (atomic download otherwise).
  2. Extracts text with `pdftotext -layout` and records page provenance.
  3. Emits data/clean/od2a_extract.json -- every quantitative + geographic fact
     hand-transcribed from the PDF, each tagged with page/section/figure and the
     exact printed wording. Chart values whose per-series attribution is NOT
     unambiguous from the layout are stored as raw, clearly-flagged token
     arrays -- NEVER invented or force-attributed.
  4. Emits data/clean/od2a_zips.geojson -- 2020 Census ZCTA polygons for every
     ZIP that appears in OD2A geography sections PLUS a required baseline set of
     12, pulled from Census TIGERweb ArcGIS REST (no 500MB national file).
     Per-ZIP properties carry the published RANKING only (the PDF ranks ZIPs
     but prints no per-ZIP counts), with page provenance and a retrieved date.

NEVER fabricates, imputes, or extrapolates. Where the PDF prints a ranking
without counts, only the ranking is recorded. Where a value is not published,
the field is null.

Run standalone from repo root:
    ./venv/bin/python scripts/pull_od2a.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import USER_AGENT  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_RAW = os.path.join(REPO_ROOT, "data", "raw")
DATA_CLEAN = os.path.join(REPO_ROOT, "data", "clean")
OD2A_RAW = os.path.join(DATA_RAW, "od2a")

PDF_URL = (
    "https://www.dallascounty.org/Assets/uploads/docs/hhs/public-health/"
    "alcohol-substance/reports/OD2A%20-%202024%20Annual%20Surveillance%20"
    "Report%20of%20Preliminary%20Trends%20in%20Drug%20Overdoses%20in%20"
    "Dallas%20County.pdf"
)
PDF_PATH = os.path.join(OD2A_RAW, "OD2A_2024_Annual_Surveillance_Report.pdf")
TXT_PATH = os.path.join(OD2A_RAW, "OD2A_2024_Annual_Surveillance_Report.txt")

EXTRACT_JSON = os.path.join(DATA_CLEAN, "od2a_extract.json")
ZIPS_GEOJSON = os.path.join(DATA_CLEAN, "od2a_zips.geojson")

TODAY = date.today().isoformat()

# TIGERweb layer 1 is the 2020 ZCTA layer; its ZCTA5 field carries the code.
TIGERWEB_ZCTA = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "PUMA_TAD_TAZ_UGA_ZCTA/MapServer/1/query"
)

SOURCE_DOC = "DCHHS OD2A 2024 Annual Surveillance Report"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_dirs():
    for d in (DATA_RAW, DATA_CLEAN, OD2A_RAW):
        os.makedirs(d, exist_ok=True)


def atomic_download(url, dest_path, timeout=300):
    tmp_path = dest_path + ".tmp"
    log(f"Downloading {url} -> {dest_path}")
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp_path, dest_path)
    log(f"Downloaded {os.path.getsize(dest_path):,} bytes")


def ensure_pdf():
    """Reuse existing PDF if present; otherwise download atomically. Always
    keep a dated raw snapshot copy alongside it for reproducibility."""
    ensure_dirs()
    if os.path.exists(PDF_PATH) and os.path.getsize(PDF_PATH) > 100_000:
        log(f"Reusing existing PDF {PDF_PATH} ({os.path.getsize(PDF_PATH):,} bytes)")
    else:
        atomic_download(PDF_URL, PDF_PATH)
    # dated snapshot
    dated = os.path.join(OD2A_RAW, f"OD2A_2024_Annual_Surveillance_Report_{TODAY}.pdf")
    if not os.path.exists(dated):
        with open(PDF_PATH, "rb") as src, open(dated + ".tmp", "wb") as dst:
            dst.write(src.read())
        os.replace(dated + ".tmp", dated)
        log(f"Wrote dated snapshot {dated}")
    else:
        log(f"Dated snapshot already present: {dated}")
    return PDF_PATH


def extract_text():
    """Extract layout-preserving text with pdftotext -layout. Page breaks are
    form-feed (0x0C) separated so we can attribute page numbers."""
    log("Running pdftotext -layout ...")
    subprocess.run(
        ["pdftotext", "-layout", PDF_PATH, TXT_PATH], check=True
    )
    text = open(TXT_PATH, "r", encoding="utf-8", errors="replace").read()
    n_pages = text.count("\f") + 1
    n_lines = text.count("\n") + 1
    log(f"Extracted {n_lines} lines across {n_pages} pages -> {TXT_PATH}")
    return text, n_pages


# ---------------------------------------------------------------------------
# The extract: every quantitative + geographic fact, hand-transcribed with
# page / section / figure provenance and the exact printed wording.
# Each fact carries: metric, value(s), unit, period, page, section, figure,
# and (where relevant) `printed` = the verbatim sentence it came from, plus
# `note` for suppression / attribution caveats.
# ---------------------------------------------------------------------------
def build_extract(n_pages):
    def cite(page, section, figure=None):
        c = {"source": SOURCE_DOC, "page": page, "section": section}
        if figure:
            c["figure"] = figure
        return c

    facts = []

    def add(**kw):
        facts.append(kw)

    # ---- Executive Summary (p3) ----
    add(id="deaths_2024", category="fatal_mortality",
        metric="Total overdose deaths, calendar year 2024 (provisional)",
        value=558, unit="deaths", period="CY2024",
        printed="As of August 14, 2025, a total of 558 overdose deaths were "
                "reported in 2024 compared to 628 in 2023.",
        **cite(3, "Executive Summary"))
    add(id="deaths_2023", category="fatal_mortality",
        metric="Total overdose deaths, calendar year 2023",
        value=628, unit="deaths", period="CY2023",
        printed="As of August 14, 2025, a total of 558 overdose deaths were "
                "reported in 2024 compared to 628 in 2023.",
        **cite(3, "Executive Summary"))
    add(id="highest_burden_groups", category="demographics",
        metric="Demographic groups with highest death burden",
        value=["males", "White individuals", "adults aged 35-64 years"],
        printed="The highest burden of deaths occurred among males, White "
                "individuals, and adults aged 35-64 years.",
        **cite(3, "Executive Summary"))
    add(id="ed_opioid_change_hispanic", category="nonfatal_ed",
        metric="Opioid-related ED visit change, Hispanic individuals, 2023-2024",
        value=2.8, unit="percent", direction="increase", period="2023-2024",
        printed="visits increased by 2.8% among Hispanic individuals, while "
                "decreasing 10.5% among non-Hispanic Black individuals and 3% "
                "among non-Hispanic White individuals.",
        **cite(3, "Executive Summary"))
    add(id="ed_opioid_change_black", category="nonfatal_ed",
        metric="Opioid-related ED visit change, non-Hispanic Black, 2023-2024",
        value=10.5, unit="percent", direction="decrease", period="2023-2024",
        printed="decreasing 10.5% among non-Hispanic Black individuals",
        **cite(3, "Executive Summary"))
    add(id="ed_opioid_change_white", category="nonfatal_ed",
        metric="Opioid-related ED visit change, non-Hispanic White, 2023-2024",
        value=3, unit="percent", direction="decrease", period="2023-2024",
        printed="3% among non-Hispanic White individuals",
        **cite(3, "Executive Summary"))

    # ---- Introduction (p3) ----
    add(id="county_population", category="context",
        metric="Dallas County population",
        value=2_600_000, unit="people", period="current",
        printed="Dallas County is the second-largest county in Texas with a "
                "population of 2.6 million people.",
        **cite(3, "Introduction"))
    add(id="households_non_english", category="context",
        metric="Households speaking a language other than English",
        value=40, unit="percent",
        printed="40% of Dallas County households speak a language other than "
                "English",
        **cite(3, "Introduction"))
    add(id="households_limited_english", category="context",
        metric="Share of those households with limited-English proficiency",
        value="approximately 1 in 5", unit="ratio",
        printed="approximately 1 in 5 of those households have limited-English "
                "proficiency, the highest percentage in the region.",
        **cite(3, "Introduction"))

    # ---- Fatal Overdose Surveillance: Demographic Trends (p4) ----
    add(id="deaths_pct_change_2016_2024", category="fatal_mortality",
        metric="Change in confirmed drug overdose deaths, 2016-2024",
        value=77.1, unit="percent", direction="increase", period="2016-2024",
        printed="Between 2016 and 2024, the number of confirmed drug overdose "
                "deaths in Dallas County increased by 77.1%.",
        **cite(4, "Fatal Overdose Surveillance - Demographic Trends", "Figure 1a"))
    add(id="deaths_by_year", category="fatal_mortality",
        metric="All drug overdose deaths by year",
        value={"2016": 315, "2017": 324, "2018": 332, "2019": 340,
               "2020": 439, "2021": 543, "2022": 549, "2023": 628, "2024": 558},
        unit="deaths", period="2016-2024",
        printed="Figure 1a. All Drug Overdose Deaths by Year in Dallas County, "
                "TX, 2016-2024 (values labeled on bars).",
        note="Per-bar labels transcribed directly from Figure 1a. Sum = 4,028, "
             "which matches the total explicitly printed on p9.",
        data_source="Texas DSHS Vital Statistics",
        **cite(4, "Fatal Overdose Surveillance - Demographic Trends", "Figure 1a"))
    add(id="crude_rate_change_2018_2024", category="fatal_mortality",
        metric="Overdose crude mortality rate change, Dallas County, 2018-2024",
        value=61, unit="percent", direction="increase", period="2018-2024",
        from_value=12.9, to_value=20.8, rate_unit="per 100,000",
        printed="Between 2018 and 2024, overdose crude mortality rates in "
                "Dallas County increased by 61%, from 12.9 to 20.8 per 100,000 "
                "individuals.",
        data_source="CDC WONDER (as of April 2, 2025)",
        **cite(4, "Fatal Overdose Surveillance - Demographic Trends", "Figure 1b"))
    add(id="crude_rate_change_texas_2018_2024", category="fatal_mortality",
        metric="Overdose crude mortality rate change, Texas, 2018-2024",
        value=49.5, unit="percent", direction="increase", period="2018-2024",
        printed="significantly outpacing the 49.5% increase seen in Texas",
        data_source="CDC WONDER (as of April 2, 2025)",
        **cite(4, "Fatal Overdose Surveillance - Demographic Trends", "Figure 1b"))
    add(id="crude_rate_change_us_2018_2024", category="fatal_mortality",
        metric="Overdose crude mortality rate change, United States, 2018-2024",
        value=9.9, unit="percent", direction="increase", period="2018-2024",
        printed="and the 9.9% increase in the United States",
        data_source="CDC WONDER (as of April 2, 2025)",
        **cite(4, "Fatal Overdose Surveillance - Demographic Trends", "Figure 1b"))
    add(id="crude_rate_fig1b_raw_tokens", category="fatal_mortality",
        metric="Figure 1b crude mortality rate chart labels (raw, unattributed)",
        value=[32.1, 32.4, 31.4, 27.9, 25.1, 22.6, 21.5, 21.8, 22.0, 20.6,
               17.1, 18.3, 20.8, 12.9, 13.1, 18.6, 16.9, 14.2, 15.7, 10.8, 10.5],
        unit="per 100,000", period="2018-2024",
        note="Raw numeric labels scraped from Figure 1b (US / Texas / Dallas "
             "County lines). Per-series/per-year attribution is NOT unambiguous "
             "from the PDF layout and is therefore NOT asserted; only the "
             "text-stated Dallas 12.9->20.8 (2018->2024) values are attributed "
             "above.",
        data_source="CDC WONDER (as of April 2, 2025)",
        **cite(5, "Fatal Overdose Surveillance - Demographic Trends", "Figure 1b"))

    # ---- Manner of death (p5, Fig 1c) ----
    add(id="accidental_change_2016_2024", category="fatal_mortality",
        metric="Accidental overdose deaths change, 2016-2024",
        value="almost 90", unit="percent", direction="increase", period="2016-2024",
        printed="Accidental overdoses made up most deaths between 2016 and "
                "2024, increasing by almost 90% during that period.",
        **cite(5, "Fatal Overdose Surveillance", "Figure 1c"))
    add(id="suicide_change_2016_2024", category="fatal_mortality",
        metric="Suicide overdose deaths change, 2016-2024",
        value=4, unit="percent", direction="increase", period="2016-2024",
        printed="deaths resulting from suicide saw a smaller increase of 4% "
                "during the same period.",
        **cite(5, "Fatal Overdose Surveillance", "Figure 1c"))

    # ---- Drug class changes (p5, Fig 2) ----
    add(id="opioid_alone_change_2016_2024", category="fatal_mortality",
        metric="Deaths from opioids alone change, 2016-2024",
        value=17, unit="percent", direction="increase", period="2016-2024",
        printed="Deaths attributed solely to opioids (no other drug present) "
                "rose by 17%",
        **cite(5, "Fatal Overdose Surveillance", "Figure 2"))
    add(id="opioid_stimulant_change_2016_2024", category="fatal_mortality",
        metric="Deaths from opioids + stimulants combined change, 2016-2024",
        value=260, unit="percent", direction="increase", period="2016-2024",
        printed="the combination of opioids and stimulants saw a dramatic "
                "increase of 260%.",
        **cite(5, "Fatal Overdose Surveillance", "Figure 2"))
    add(id="stimulant_alone_change_2016_2024", category="fatal_mortality",
        metric="Deaths from stimulants alone change, 2016-2024",
        value=167, unit="percent", direction="increase", period="2016-2024",
        printed="Deaths from stimulants alone climbed by 167%.",
        **cite(5, "Fatal Overdose Surveillance", "Figure 2"))

    # ---- Specific drug changes (p6, Fig 3) ----
    add(id="fentanyl_change_2016_2024", category="fatal_mortality",
        metric="Fentanyl-related deaths change, 2016-2024",
        value=1550, unit="percent", direction="increase", period="2016-2024",
        printed="From 2016 to 2024, fentanyl deaths surged by an alarming 1,550%.",
        **cite(6, "Fatal Overdose Surveillance", "Figure 3"))
    add(id="meth_change_2016_2024", category="fatal_mortality",
        metric="Methamphetamine-related deaths change, 2016-2024",
        value=319, unit="percent", direction="increase", period="2016-2024",
        printed="methamphetamine-related deaths increased by 319%",
        **cite(6, "Fatal Overdose Surveillance", "Figure 3"))
    add(id="cocaine_change_2016_2024", category="fatal_mortality",
        metric="Cocaine-related deaths change, 2016-2024",
        value=148, unit="percent", direction="increase", period="2016-2024",
        printed="cocaine increased by 148%",
        **cite(6, "Fatal Overdose Surveillance", "Figure 3"))
    add(id="heroin_change_2016_2024", category="fatal_mortality",
        metric="Heroin-related deaths change, 2016-2024",
        value=69, unit="percent", direction="decrease", period="2016-2024",
        printed="There has been a decrease in deaths due to heroin by 69%",
        **cite(6, "Fatal Overdose Surveillance", "Figure 3"))
    add(id="treatment_opioids_change_2016_2024", category="fatal_mortality",
        metric="Treatment (prescription) opioid deaths change, 2016-2024",
        value=31, unit="percent", direction="decrease", period="2016-2024",
        printed="and treatment opioids by 31%",
        **cite(6, "Fatal Overdose Surveillance", "Figure 3"))

    # ---- Deaths by sex (p7, Fig 4) -- clean two-series, sums match totals ----
    add(id="deaths_by_sex", category="demographics",
        metric="All drug overdose deaths by sex and year",
        value={
            "female": {"2016": 105, "2017": 96, "2018": 101, "2019": 97,
                       "2020": 117, "2021": 177, "2022": 155, "2023": 163,
                       "2024": 145},
            "male": {"2016": 210, "2017": 228, "2018": 231, "2019": 243,
                     "2020": 322, "2021": 366, "2022": 394, "2023": 465,
                     "2024": 413},
        },
        unit="deaths", period="2016-2024",
        printed="Figure 4. All Drug Overdose Deaths by Sex (bar labels).",
        note="Two-series bar labels transcribed from Figure 4; per-year "
             "female+male sums equal the Figure 1a annual totals, confirming "
             "the transcription.",
        data_source="Texas DSHS Vital Statistics",
        **cite(7, "Demographic Trends", "Figure 4"))
    add(id="deaths_by_ethnicity_fig5", category="demographics",
        metric="All drug overdose deaths by ethnicity (Fig 5, raw tokens)",
        value=None,
        printed="Figure 5. All Drug Overdose Deaths by Ethnicity (Black, "
                "Hispanic, Other, White), 2016-2024.",
        note="Figure 5 is a stacked/grouped bar whose per-series-per-year "
             "labels cannot be unambiguously attributed from the PDF text "
             "layout; per the no-extrapolation rule, individual counts are NOT "
             "recorded. Qualitative finding: highest deaths among White "
             "population (see p6 narrative).",
        data_source="Texas DSHS Vital Statistics",
        **cite(7, "Demographic Trends", "Figure 5"))

    # ---- Deaths by age (p8, Fig 6) ----
    add(id="deaths_by_age_fig6", category="demographics",
        metric="All drug overdose deaths by age group (Fig 6, raw)",
        value=None,
        printed="Figure 6. All Drug Overdose Deaths by Age group, 2016-2024 "
                "(groups 0-17,18-24,25-34,35-44,45-54,55-64,65+).",
        note="Stacked bar; per-series-per-year labels not unambiguously "
             "attributable from layout, so counts are not recorded. Narrative "
             "(p6): highest among individuals aged 35-64.",
        data_source="Texas DSHS Vital Statistics",
        **cite(8, "Demographic Trends", "Figure 6"))

    # ---- Education (p8, Fig 7) -- clearly labeled categories ----
    add(id="deaths_by_education", category="demographics",
        metric="All drug overdose deaths by educational status (share of total)",
        value={
            "High School Graduate / GED": 43.1,
            "9th - 12th grade (No diploma)": 17.8,
            "Some College (No Degree)": 15.7,
            "8th grade or less": 7.6,
            "Education Unknown": 7.3,
            "Bachelor's degree": 6.5,
            "Doctorate / Professional Degree": 6.3,
            "Associate degree": 4.4,
            "Master's Degree": 2.0,
        },
        unit="percent", period="2016-2024",
        printed="overdose deaths were most prevalent among individuals who "
                "completed high school or had a GED (43.1%), followed by those "
                "who completed 9th-12th grade (17.8%).",
        note="Percentages transcribed directly from labeled bars in Figure 7.",
        data_source="Texas DSHS Vital Statistics",
        **cite(8, "Demographic Trends", "Figure 7"))
    add(id="marital_status_ranking", category="demographics",
        metric="Marital status with highest overdose deaths (ranking)",
        value=["Never Married", "Divorced"],
        printed="the highest number of overdose deaths occurred among "
                "individuals who had never been married, followed by those who "
                "were divorced.",
        note="Ranking only; Figure 8 prints no per-category counts.",
        **cite(8, "Demographic Trends", "Figure 8"))

    # ---- Geographic Trends (p9-10) ----
    add(id="top_residence_zips_deaths", category="geography",
        metric="Top residence ZIP codes by overdose deaths, 2016-2024 (ranking)",
        value=["75217", "75216", "75215"], unit="rank_order", period="2016-2024",
        printed="ZIP codes 75217, 75216, and 75215 recorded the highest number "
                "of drug overdose deaths between 2016 and 2024.",
        note="Ranking only; Figure 9 map prints no per-ZIP counts.",
        data_source="Texas DSHS Vital Statistics",
        **cite(9, "Geographic Trends", "Figure 9"))
    add(id="total_deaths_window", category="fatal_mortality",
        metric="Total overdose deaths, 2016-2024 window",
        value=4028, unit="deaths", period="2016-2024",
        printed="approximately 60.9% of the total 4,028 overdose deaths in "
                "Dallas County occurred within the city of Dallas.",
        note="Explicitly printed total; equals the sum of Figure 1a annual "
             "bar labels.",
        **cite(9, "Geographic Trends", "Figure 10"))
    add(id="city_of_dallas_share", category="geography",
        metric="Share of overdose deaths within City of Dallas, 2016-2024",
        value=60.9, unit="percent", period="2016-2024", count=2455,
        printed="approximately 60.9% of the total 4,028 overdose deaths in "
                "Dallas County occurred within the city of Dallas, accounting "
                "for 2,455 fatalities.",
        **cite(9, "Geographic Trends", "Figure 10"))

    # ---- Fentanyl narrative (p10-11) ----
    add(id="fentanyl_findings", category="demographics",
        metric="Fentanyl-related death demographic findings",
        value={
            "2016_2024_highest": "White individuals, particularly males, "
                                 "ages 25-34",
            "2024_highest_combined_sex_race": "Hispanic males",
            "2016_2024_by_age_race": "Hispanic aged 25-34 highest, then White "
                                     "aged 35-44",
        },
        printed="Between 2016 and 2024, fentanyl-related deaths were highest "
                "among White individuals, particularly males and those in the "
                "25-34 age group. In 2024, however, Hispanic males had the "
                "highest number of fentanyl-related deaths across all combined "
                "sex and race/ethnicity categories.",
        note="Figures 11a-c print grouped-bar labels not unambiguously "
             "attributable from layout; only the narrative findings recorded.",
        data_source="Texas DSHS Vital Statistics",
        **cite(10, "Substance Specific Overdose Deaths - Fentanyl",
               "Figures 11a-11c"))

    # ---- Cocaine narrative (p12-13) ----
    add(id="cocaine_findings", category="demographics",
        metric="Cocaine-related death demographic findings",
        value={
            "2016_2024_highest": "Black males, particularly aged 55-64",
            "2024_highest_age_sex": "males aged 55-64, then males aged 25-34",
            "2024_highest_age_race": "Black individuals aged 55-64, then Black "
                                     "individuals aged 65+",
            "consistent": "Black males highest, then Hispanic males, then Black "
                          "females",
        },
        printed="Between 2016 and 2024, the highest number of cocaine-related "
                "deaths occurred among Black males, particularly those aged "
                "55-64. ... Black males consistently accounted for the highest "
                "number of overdose deaths, followed by Hispanic males and "
                "Black females.",
        note="Hispanic females suppressed (<10) in both years and not "
             "displayed. Figures 12a-c grouped-bar labels not unambiguously "
             "attributable; only narrative recorded.",
        data_source="Texas DSHS Vital Statistics",
        **cite(12, "Substance Specific Overdose Deaths - Cocaine",
               "Figures 12a-12c"))

    # ---- Methamphetamine narrative (p13-15) ----
    add(id="meth_findings", category="demographics",
        metric="Methamphetamine-related death demographic findings",
        value={
            "2023_2024_trend": "declined overall",
            "highest": "White individuals, males, adults aged 55-64",
            "increase_group": "Hispanic males",
            "2024_highest": "White males aged 55-64",
        },
        printed="Between 2023 and 2024, methamphetamine-related deaths declined "
                "overall, with the highest numbers observed among White "
                "individuals, males, and adults aged 55-64. However, deaths "
                "increased among Hispanic males over the same period.",
        note="Figures 13a-c grouped-bar labels not unambiguously attributable; "
             "only narrative recorded.",
        data_source="Texas DSHS Vital Statistics",
        **cite(13, "Substance Specific Overdose Deaths - Methamphetamine",
               "Figures 13a-13c"))

    # ---- Non-fatal ED surveillance (p15-18) ----
    add(id="ed_alldrug_change_2018_2024", category="nonfatal_ed",
        metric="All-drug overdose ED visits change, 2018-2024",
        value=51.4, unit="percent", direction="increase", period="2018-2024",
        printed="The total number of all drug overdose-related EDVs increased "
                "by 51.4% between 2018 and 2024.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(15, "Non-Fatal Overdose Surveillance", "Figure 14"))
    add(id="ed_alldrug_change_2023_2024", category="nonfatal_ed",
        metric="All-drug overdose ED visits change, 2023-2024",
        value=5.2, unit="percent", direction="decrease", period="2023-2024",
        printed="Between 2023 and 2024, it declined slightly by 5.2%.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(15, "Non-Fatal Overdose Surveillance", "Figure 14"))
    add(id="ed_alldrug_by_year", category="nonfatal_ed",
        metric="All-drug overdose ED visits by year",
        value={"2018": 2419, "2019": 2608, "2020": 2568, "2021": 3179,
               "2022": 3285, "2023": 3863, "2024": 3663},
        unit="ED visits", period="2018-2024",
        printed="Figure 14. All Drug Overdose EDVs by Year (bar labels).",
        note="Bar labels transcribed from Figure 14; 3663/2419 = 1.514 "
             "confirms the printed +51.4% (2018-2024).",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(16, "Historical Trends of Drug Overdose Emergency Visits by Year",
               "Figure 14"))
    add(id="ed_opioid_2024", category="nonfatal_ed",
        metric="Opioid-related ED visits, 2024",
        value=793, unit="ED visits", period="2024", change_pct=5.7,
        change_direction="decrease",
        printed="In 2024, the total number of opioid-related emergency "
                "department visits was 793, reflecting a 5.7% decline compared "
                "to 2023.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(16, "Historical Trends", "Figure 15a"))
    add(id="ed_opioid_by_year", category="nonfatal_ed",
        metric="Opioid-related ED visits by year",
        value={"2023": 841, "2024": 793}, unit="ED visits", period="2023-2024",
        printed="Figure 15a. Opioid-related Overdose EDVs by Year (bar labels "
                "841, 793).",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(16, "Historical Trends", "Figure 15a"))
    add(id="ed_opioid_q4_2024_change", category="nonfatal_ed",
        metric="Opioid-related ED visits change, Q4 2024",
        value=7.1, unit="percent", direction="decrease", period="2024 Q4",
        printed="opioid overdose-related EDVs showed fluctuations, ultimately "
                "declining by 7.1% in the fourth quarter of 2024.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(16, "Historical Trends", "Figure 15b"))
    add(id="ed_opioid_quarterly_raw_tokens", category="nonfatal_ed",
        metric="Opioid-related ED visits by quarter (Fig 15b, raw tokens)",
        value=[212, 227, 218, 184, 216, 212, 197, 168],
        unit="ED visits", period="2023 Q1 - 2024 Q4",
        note="Raw quarterly bar labels scraped from Figure 15b. Exact "
             "quarter-to-value mapping is not unambiguous from the layout, so "
             "no per-quarter attribution is asserted; only the text-stated Q4 "
             "2024 -7.1% is recorded above.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(17, "Historical Trends", "Figure 15b"))
    add(id="ed_opioid_monthly_peaks", category="nonfatal_ed",
        metric="Opioid-related ED visit monthly peaks",
        value={"2024_peaks": ["March", "May", "October"],
               "2023_peaks": ["March", "June", "September"]},
        printed="peaks observed in March, May, and October of 2024, and in "
                "March, June, and September of 2023.",
        note="Figure 16 monthly bar labels not unambiguously attributable; "
             "only the peak months are recorded.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(17, "Historical Trends", "Figure 16"))
    add(id="ed_opioid_age_share_2024", category="nonfatal_ed",
        metric="Share of opioid ED visits among ages 18-44, 2024",
        value=69.2, unit="percent", period="2024",
        groups=["18-24", "25-34", "35-44"],
        printed="In 2024, individuals aged 18-24, 25-34, and 35-44 collectively "
                "accounted for nearly 69.2% of all opioid overdose-related ED "
                "visits.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(17, "Historical Trends", "Figure 17"))
    add(id="ed_opioid_sex_ratio_2024", category="nonfatal_ed",
        metric="Male-to-female ratio of opioid ED visits, 2024",
        value=2.1, unit="ratio (male:female)", period="2024",
        printed="Males had nearly 2.1 times as many opioid overdose-related "
                "EDVs as females.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(17, "Historical Trends", "Figure 17"))
    add(id="ed_opioid_top_zips_2024", category="geography",
        metric="Top residence ZIPs by opioid ED visits, 2024 (ranking)",
        value=["75235", "75243", "75228", "75042", "75217", "75230"],
        unit="rank_order", period="2024",
        printed="Geospatial analysis in 2024 showed the number of opioid-"
                "related emergency department visits was highest among "
                "residents of zip code 75235, followed by 75243, 75228, 75042, "
                "75217, 75230.",
        note="Ranking only; Figure 21 map prints no per-ZIP counts.",
        data_source="Texas DSHS Syndromic Surveillance (ESSENCE)",
        **cite(18, "Historical Trends - Geospatial analysis", "Figure 21"))

    # ---- Conclusion (p19) ----
    add(id="conclusion_top_zips", category="geography",
        metric="ZIPs with highest overdose fatality concentration (conclusion)",
        value=["75217", "75216", "75215"],
        printed="certain areas in Dallas County, notably ZIP codes 75217, "
                "75216, and 75215, have been identified as having the highest "
                "concentrations of overdose fatalities.",
        **cite(19, "Conclusion"))
    add(id="conclusion_age_burden", category="demographics",
        metric="Age groups bearing highest death burden (conclusion)",
        value="aged 25-54 years",
        printed="with males and specific age groups, including those aged 25-54 "
                "years, consistently bearing the highest overdose death burden.",
        **cite(19, "Conclusion"))

    # ---- Data sources / reporting periods (p19) ----
    data_sources = {
        "mortality": "Texas Department of State Health Services Vital "
                     "Statistics. Data as of August 14, 2025.",
        "comparative_rates": "CDC Wide-ranging Online Data for Epidemiologic "
                             "Research (WONDER). Data extracted April 2, 2025.",
        "emergency_department": "Electronic Surveillance System for the Early "
                                "Notification of Community-based Epidemics "
                                "(ESSENCE). Data as of August 14, 2025.",
    }

    extract = {
        "document": {
            "title": "Overdose Data to Action: 2024 Annual Surveillance Report "
                     "of Preliminary Trends in Drug Overdoses in Dallas County",
            "publisher": "Dallas County Health & Human Services (DCHHS)",
            "short_source": SOURCE_DOC,
            "url": PDF_URL,
            "n_pages": n_pages,
            "period_covered": "2016-2024 (fatal), 2018-2024 / 2023-2024 (non-fatal)",
        },
        "provenance_note": (
            "Every value below is an OBSERVED, PUBLISHED figure transcribed "
            "verbatim from the OD2A PDF with page/section/figure citation. No "
            "value is estimated, imputed, or extrapolated. Rankings without "
            "per-unit counts are recorded as rankings only. Chart labels that "
            "cannot be unambiguously attributed by series/year from the PDF "
            "text layout are stored as flagged raw token arrays, not attributed "
            "values. Counts < 10 are suppressed in the source and noted."
        ),
        "data_sources": data_sources,
        "retrieved": TODAY,
        "n_facts": len(facts),
        "facts": facts,
    }
    return extract


# ---------------------------------------------------------------------------
# ZCTA polygons via Census TIGERweb ArcGIS REST (no national file).
# ---------------------------------------------------------------------------
def fetch_zctas(zips):
    where = "ZCTA5 IN (" + ",".join("'%s'" % z for z in zips) + ")"
    params = {
        "where": where,
        "outFields": "ZCTA5,GEOID",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    log(f"Querying TIGERweb ZCTA layer for {len(zips)} ZIPs ...")
    r = requests.get(TIGERWEB_ZCTA, params=params, timeout=120)
    r.raise_for_status()
    gj = r.json()
    feats = gj.get("features", [])
    log(f"  received {len(feats)} ZCTA features")
    return feats


def build_geojson(extract):
    # Collect ZIPs appearing in OD2A geography facts.
    death_rank = ["75217", "75216", "75215"]                       # Fig 9 / p9
    ed_rank = ["75235", "75243", "75228", "75042", "75217", "75230"]  # Fig 21 / p18
    required_12 = ["75215", "75210", "75211", "75212", "75203", "75224",
                   "75201", "75204", "75226", "75217", "75227", "75216"]

    all_zips = []
    for z in death_rank + ed_rank + required_12:
        if z not in all_zips:
            all_zips.append(z)
    all_zips.sort()

    feats = fetch_zctas(all_zips)
    by_zip = {}
    for f in feats:
        z = str(f["properties"].get("ZCTA5"))
        by_zip[z] = f

    missing = [z for z in all_zips if z not in by_zip]
    if missing:
        log(f"WARNING: no polygon returned for: {missing}")

    death_source = (f"{SOURCE_DOC}, p.9 (Figure 9)")
    ed_source = (f"{SOURCE_DOC}, p.18 (Figure 21)")

    out_feats = []
    for z in all_zips:
        f = by_zip.get(z)
        if f is None:
            continue
        d_rank = death_rank.index(z) + 1 if z in death_rank else None
        e_rank = ed_rank.index(z) + 1 if z in ed_rank else None

        srcs = []
        if d_rank is not None:
            srcs.append(death_source)
        if e_rank is not None:
            srcs.append(ed_source)

        props = {
            "zcta": z,
            # published rankings only -- the PDF prints NO per-ZIP counts
            "od2a_death_residence_rank_2016_2024": d_rank,
            "od2a_death_residence_count_2016_2024": None,   # not published
            "od2a_opioid_ed_visits_rank_2024": e_rank,
            "od2a_opioid_ed_visits_count_2024": None,       # not published
            "in_required_baseline_12": z in required_12,
            "source": "; ".join(srcs) if srcs else None,
            "geometry_source": "US Census Bureau TIGERweb 2020 ZCTA (ArcGIS REST)",
            "retrieved": TODAY,
        }
        out_feats.append({
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": props,
        })

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "description": "2020 Census ZCTA polygons for ZIP codes appearing "
                           "in DCHHS OD2A 2024 geography sections plus a "
                           "required 12-ZIP baseline. Properties carry ONLY "
                           "published rankings; per-ZIP counts are null because "
                           "the OD2A report prints none.",
            "source_document": SOURCE_DOC,
            "geometry_source": TIGERWEB_ZCTA,
            "n_features": len(out_feats),
            "retrieved": TODAY,
            "od2a_death_residence_rank_zips": death_rank,
            "od2a_opioid_ed_visits_rank_zips": ed_rank,
            "required_baseline_12": required_12,
        },
        "features": out_feats,
    }
    return geojson


def main():
    ensure_dirs()
    log("=== OD2A extract pipeline start ===")
    ensure_pdf()
    text, n_pages = extract_text()

    extract = build_extract(n_pages)
    with open(EXTRACT_JSON, "w") as f:
        json.dump(extract, f, indent=2)
    log(f"Wrote {EXTRACT_JSON} ({os.path.getsize(EXTRACT_JSON):,} bytes, "
        f"{extract['n_facts']} facts)")

    geojson = build_geojson(extract)
    with open(ZIPS_GEOJSON, "w") as f:
        json.dump(geojson, f)
    log(f"Wrote {ZIPS_GEOJSON} ({os.path.getsize(ZIPS_GEOJSON):,} bytes, "
        f"{geojson['metadata']['n_features']} features)")

    # Validate the geojson loads with geopandas.
    import geopandas as gpd
    gdf = gpd.read_file(ZIPS_GEOJSON)
    assert not gdf.empty, "geojson loaded empty"
    assert gdf.geometry.notna().all(), "null geometry present"
    log(f"geopandas validation OK: {len(gdf)} features, CRS={gdf.crs}, "
        f"all geometries valid={gdf.geometry.is_valid.all()}")

    log("=== OD2A extract pipeline done ===")
    return extract, geojson


if __name__ == "__main__":
    main()
