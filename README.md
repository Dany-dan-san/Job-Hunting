# Job Hunting — Job Intelligence Pipeline

A personal Python project for turning job-board listings into a structured, reviewable application shortlist. The workflow combines **CSV consolidation, bilingual occupation classification, rule-based prioritization, manual screening, vacancy-detail evidence extraction, and application planning**.

It was developed primarily to support a search for early-career data, analytics, research, and software roles in Prague. The code is a **decision-support pipeline**, not an automatic job-application bot: suitability decisions and application choices remain with the user.

## How the pipeline works

```text
Job-board spiders / *_basic.csv exports
                  |
                  v
01  Merge raw listings -----------------> register_basic.csv
                  |
                  v
01b Extract distinct job titles --------> unique_job_titles.csv
                  |
                  v
02  Classify root occupations ----------> root_classified_titles.csv
                  |
                  v
03  Classify subtypes and tags ---------> job_classes.csv
                  |
                  v
04  Enrich original register -----------> register_enriched.csv
                  |
                  v
05  Score and build review queues ------> priority and specialist CSV queues
                  |
                  v
06  Open vacancies; manually grade -----> evaluation.csv (grades 1–4)
                  |
                  v
07  Keep grades 1 and 2 ----------------> prospects_gross.csv
                  |
                  v
    Vacancy-page enrichment* -----------> prospects_enriched_raw.csv
                  |
                  v
08  Extract auditable machine evidence -> prospect_evidence.csv
                  |
                  v
09  Review in Streamlit ----------------> prospect_reviews.csv
                                      + application_queue.csv (APPLY / STRETCH)
                  |
                  v
10  Group application sessions ---------> application_queue_grouped.csv
```

*The vacancy-page enrichment process that creates `prospects_enriched_raw.csv` is **separate from the numbered scripts documented here**. Step 08 depends on that file; it does **not** scrape vacancy pages itself. The Streamlit app in step 09 can also display gross prospects if enrichment is missing, but without the extra vacancy facts and evidence.

## Scripts and responsibilities

| Step | Script | Actual responsibility |
| --- | --- | --- |
| **01** | `01_merge_raw_data.py` | Reads direct-child `*_basic.csv` exports from `data/01_raw/`, aligns columns and rebuilds `register_basic.csv`. Repeated IDs/URLs are **reported but retained**, not deduplicated. |
| **01b** | `01b_extract_unique_titles.py` | Produces a distinct list of nonblank titles, ignoring case and surplus whitespace, to avoid classifying every repeated title individually. |
| **02** | `02_unify_job_titles.py` | Uses deterministic, accent-insensitive Czech/English rules to map titles to a **root occupation** (`Analyst`, `Developer`, `Manager`, etc.). Can print a random validation sample and optionally save an audit of classification evidence. |
| **03** | `03_classify_jobs_titles.py` | Adds **position subtypes**, optional secondary subtypes, business/domain tags, technology tags and title-derived seniority, without confusing a technology or domain with the occupation itself. |
| **04** | `04_enrich_register.py` | Joins the title taxonomy back to each original listing. Preserves original listings and prioritizes source-reported seniority over title-inferred seniority. `--strict` can reject unmatched titles. |
| **05** | `05_build_review_queues.py` | Derives `Fit.Score`, `Review.Priority`, `Primary.Track`, location assessment, mining age, fit reasons and caution flags; exports multiple targeted review queues. |
| **06** | `06_review_prospects.py` | Opens vacancy URLs in the browser, collects a **manual grade from 1 to 4**, and immediately appends grades to `evaluation.csv`. Already-evaluated IDs are skipped on later sessions. |
| **07** | `07_filter_prospects.py` | Selects manually reviewed grades **1 and 2**, joins company and mining-date details from the basic register by ID, and writes `prospects_gross.csv`. |
| **08** | `08_extract_prospect_evidence.py` | Reads **already enriched** vacancy-page data and produces a separate, deterministic `Auto_*` evidence file. Does **not** assign suitability scores or make apply/reject decisions. |
| **09** | `09_grade_prospects.py` | A **Streamlit human-review interface**, not a batch grading script. Combines gross prospects, optional extracted page facts, machine evidence and saved human reviews; produces `prospect_reviews.csv` and the APPLY/STRETCH `application_queue.csv`. |
| **10** | `10_group_applications.py` | Organizes the application queue into role families, suggests CV and cover-letter **variant labels**, groups repeat employers into application batches and writes `application_queue_grouped.csv`. It does not create or submit applications. |

> **Filename note:** Steps 08–10 use the clean filenames shown above. If your local copies include duplicate-download suffixes such as `(1)` or `(3)`, adjust the commands or rename those files before running.

## The three levels of assessment

**1. Automatic pre-screening — Step 05.** Rules identify career tracks and signals in the **listing title, subtype, tags, seniority and other register fields**. They reward relevant technical/research terms and junior opportunities, flag seniority and location uncertainty, and penalize or exclude certain explicitly detected conflicts. Scores help determine *review order*; they are not verified measures of employability. Output priority labels include `High`, `Good`, `Exploratory`, `Senior/Leadership Stretch`, `Deprioritized`, and `Exclude`.

**2. First human selection — Steps 06–07.** Each opened listing receives one of four grades: **1 = Perfect Fit; 2 = Good Fit; 3 = Poor Fit; 4 = Disqualifying Fit**. Only grades **1 and 2** enter `prospects_gross.csv`.

**3. Detailed vacancy and application review — Steps 08–10.** Step 08 extracts reviewable signals (Czech/English requirements, minimum experience, seniority, Python/SQL/ML/statistics/R, remote working, availability, publication/freshness, and possible conflicts), together with source-text evidence. In step 09, a person separately records fit, interest, language, seniority and technology assessments, priority, notes and the final decision (`APPLY`, `STRETCH`, `HOLD`, or `REJECT`). Only `APPLY` and `STRETCH` flow into the application queue. Machine signals **never overwrite** those decisions.

## Outputs and directory layout

The scripts use the following stage-oriented data paths:

```text
data/
├── 01_raw/
│   └── *_basic.csv                    # Job-board/spider exports
├── 02_consolidated/
│   └── register_basic.csv
├── 03_taxonomy/
│   ├── unique_job_titles.csv
│   ├── root_classified_titles.csv
│   └── job_classes.csv
├── 04_enriched/
│   └── register_enriched.csv
├── 05_review_queues/
│   ├── 00_all_jobs_scored.csv
│   ├── 01_priority_review.csv
│   ├── 02_data_science_ml.csv
│   ├── ...                             # Other specialist/watchlist queues
│   ├── 13_deprioritized_or_excluded.csv
│   └── filter_summary.csv
├── 06_review/
│   └── evaluation.csv
└── 07_prospects/
    ├── prospects_gross.csv
    ├── prospects_enriched_raw.csv      # From separate vacancy-page enrichment
    ├── prospect_evidence.csv           # Automated evidence, kept separate
    ├── prospect_reviews.csv            # Saved human decisions
    ├── application_queue.csv           # APPLY / STRETCH only
    └── application_queue_grouped.csv   # Application families / document variants
```

Files are generally CSVs encoded as UTF-8 (many written with a BOM to improve compatibility with Excel and Czech characters). Some intermediate outputs are rebuilt on each run; **`evaluation.csv` records manual grades incrementally**, while Streamlit maintains its own separate review register.

## Installation

Use **Python 3.10+** and create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install pandas streamlit scrapy
```

The provided processing scripts **01–07 use only the Python standard library**. Steps **08 and 10 require Pandas**; step **09 requires Pandas and Streamlit**. Scrapy is used by the upstream spiders, which were not included in this particular code review. A separate vacancy-page extractor may have additional dependencies.

If a complete repository-level `requirements.txt` is maintained, install it instead with `python -m pip install -r requirements.txt`.

## Running the pipeline

The examples below assume you are in the directory containing the numbered scripts, and that the `data/` tree above exists under your project root.

**Important configuration:** Most scripts have default paths pointing to the original Windows development directory (a local Windows path under the original development machine). Steps 01–04 and 08/10 accept CLI path overrides. **Step 05 and step 07 have hard-coded paths**, which must be edited when running elsewhere; step 06 also uses a hard-coded default review folder, but permits an absolute input path and a custom evaluation file. Steps 08–10 locate a `Data_Mining` ancestor, or fall back to the current working directory when one is not found.

### 1. Prepare listings

Run your available spiders separately so their `*_basic.csv` files are in `data/01_raw/`. Expected source fields are `ID`, `Job Title`, `Company`, `Location`, `Salary`, and `URL`; `Seniority` and `Mining_Date` are optional in the merger. This repository segment **does not by itself perform web scraping**.

### 2. Consolidate and classify titles

```powershell
python 01_merge_raw_data.py --input-dir data/01_raw --output data/02_consolidated/register_basic.csv
python 01b_extract_unique_titles.py --input data/02_consolidated/register_basic.csv --output data/03_taxonomy/unique_job_titles.csv
python 02_unify_job_titles.py --input data/03_taxonomy/unique_job_titles.csv --output data/03_taxonomy/root_classified_titles.csv --sample-size 20
python 03_classify_jobs_titles.py --input data/03_taxonomy/root_classified_titles.csv --output data/03_taxonomy/job_classes.csv --sample-size 20
python 04_enrich_register.py --register data/02_consolidated/register_basic.csv --classes data/03_taxonomy/job_classes.csv --output data/04_enriched/register_enriched.csv
```

The output of **02** is the *root occupation* layer, not a final, fully enriched title. The output of **03** is the *detailed classification* layer. Step **04** restores those classifications to individual listings. For a stricter taxonomy completeness check, add `--strict` to step 04.

### 3. Build queues and perform first review

After updating the `INPUT_FILE` and `OUTPUT_DIR` constants in step 05 if necessary:

```powershell
python 05_build_review_queues.py
python 06_review_prospects.py 01_priority_review.csv
python 07_filter_prospects.py
```

Step 06 defaults to `data/05_review_queues/` for relative filenames and stores answers in `data/06_review/evaluation.csv`. To use nondefault locations, supply an **absolute CSV path** and `--evaluation-file`. It can also run with `--random --seed 42`. Step 07 then writes only grades 1 and 2 to `data/07_prospects/prospects_gross.csv`.

### 4. Add vacancy-page details and evidence

Supply `data/07_prospects/prospects_enriched_raw.csv` using the separate vacancy-page enrichment process. At minimum, step 08 expects the columns `ID` and `Input_URL`; it looks for additional text and metadata columns such as `Description`, `Requirements`, `Languages`, `Skills`, publication dates, and extraction status to produce useful evidence.

```powershell
python 08_extract_prospect_evidence.py --input data/07_prospects/prospects_enriched_raw.csv --output data/07_prospects/prospect_evidence.csv
```

For reproducible freshness evaluation, step 08 also supports `--as-of 2026-10-08T00:00:00Z` (example timestamp). Its outputs are **advisory categorical signals with traceable text**, not a numerical suitability score.

### 5. Review prospects in Streamlit and group applications

Run from the **project root**, so the Streamlit app finds the expected `data/07_prospects/` folder:

```powershell
streamlit run 09_grade_prospects.py
```

Use the interface to review vacancy facts and machine signals, then save individual assessments and application decisions. Saving updates `prospect_reviews.csv` and rebuilds `application_queue.csv` from `APPLY` / `STRETCH` decisions. Finally:

```powershell
python 10_group_applications.py --input data/07_prospects/application_queue.csv --output data/07_prospects/application_queue_grouped.csv
```

Step 10 groups jobs into families such as `DATA_SCIENCE_MODELLING`, `ANALYTICS_INSIGHTS`, `DATA_ENGINEERING_DWH`, `SOLUTION_SYSTEMS_CONSULTING`, `SOFTWARE_BACKEND_FULLSTACK`, and `MARKETING_TECH_AUTOMATION`. It assigns *suggested document-variant identifiers* and employer batches for a more organized manual application session.

## Important boundaries and limitations

- **Acquisition is separate:** These numbered stages start with CSV exports; the Scrapy spiders and the process producing `prospects_enriched_raw.csv` are separate components.
- **The merge is not a deduplicator:** Step 01 preserves repeated listing IDs and URLs while reporting them. Downstream review stages employ their own ID/URL handling.
- **Title classification is heuristic:** Rules work across Czech and English titles, but unusual job names, mixed titles and vague advertisements can be misclassified. Review taxonomy samples and low-confidence audit cases.
- **Two dates serve different purposes:** `Mining_Date` records collection time, **not necessarily posting time**. The later evidence layer uses vacancy-page publication evidence where available.
- **Early screening has limited evidence:** Step 05 operates on register fields and does not fully verify qualifications or language accessibility; full-text evidence is considered later in step 08/09.
- **Automated evidence does not decide:** Flags may be incomplete or uncertain; only explicit human choices determine final application-queue admission.
- **Configuration is local:** Hard-coded Windows paths and expected CSV schemas require adjustment for portability. Close CSVs in Excel before regenerating files on Windows.
- **Scraping may be restricted:** Selectors, site access and HTML formats change. Follow each website's applicable rules and use conservative request rates.

## Project status

**Personal project — ongoing development (2026).** The numbered scripts implement a staged workflow from listing consolidation to application grouping, with separate manual review and machine-evidence layers. It is built for personal use and requires local configuration, upstream data collection and the separate detailed-page enrichment step for the complete flow.
