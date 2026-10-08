r"""Map unique job-title variants to a reusable canonical position taxonomy.

Default input:
    C:\Users\demps\Job_Scanner\Data_Mining\data\diagnostics\unique_job_titles\unique_job_titles.csv

Default output:
    C:\Users\demps\Job_Scanner\Data_Mining\data\diagnostics\unique_job_titles\unified_job_titles.csv

The classifier is deterministic, accent-insensitive, and designed for mixed
Czech/English job-board data. Detailed rules are evaluated internally and then
collapsed to root occupation types for the Unified Position output. This keeps
the detailed layer available for a future subtype column. Edit TITLE_RULES,
ROOT_POSITION_GROUPS, or EXACT_OVERRIDES to refine the taxonomy later.
No third-party packages are required.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_INPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\03_taxonomy\unique_job_titles.csv"
)
DEFAULT_OUTPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\03_taxonomy\root_classified_titles.csv"

)

INPUT_COLUMN = "Job Title"
OUTPUT_FIELDS = ["Original Job Title", "Unified Position"]
AUDIT_FIELDS = [
    "Original Job Title",
    "Unified Position",
    "Detailed Subtype",
    "Confidence",
    "Decision Method",
    "Winning Signal",
    "Alternative Roots",
]


@dataclass(frozen=True)
class TitleRule:
    """One ordered detailed-position rule."""

    unified_position: str
    patterns: tuple[str, ...]

    def matches(self, normalized_title: str) -> bool:
        return any(re.search(pattern, normalized_title) for pattern in self.patterns)


@dataclass(frozen=True)
class RootSignal:
    """One explicit root-occupation signal used by the scoring engine."""

    root: str
    name: str
    score: int
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class OccupationCandidate:
    """A root occupation detected at a concrete position in a title."""

    root: str
    signal: str
    score: int
    start: int
    end: int


@dataclass(frozen=True)
class ClassificationResult:
    """Explainable result used by the CSV and optional audit output."""

    root: str
    subtype: str
    confidence: float
    method: str
    signal: str
    alternatives: tuple[str, ...] = ()


def normalize_text(value: str | None) -> str:
    """Return lower-case, accent-free text with stable word boundaries."""

    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return " ".join(text.split())


def normalize_header(value: str | None) -> str:
    if value is None:
        return ""
    return normalize_text(value.removeprefix("\ufeff").replace("_", " "))


# Exact exceptions are checked before the general rules. Keys must use the
# normalized form produced by normalize_text().
EXACT_OVERRIDES: dict[str, str] = {
    "manazerka obchodu": "Manager",
    "manazer obchodu": "Manager",
    "sales representatives consultant energy management system": "Sales Representative",
    "tvoje it kariera zacina uz behem studia salesforce zkraceny uvazek": "Other",
}


# Explicit occupational nouns and audited semantic phrases take precedence over
# contextual terms elsewhere in a title. For example, "controller" must not be
# converted to Analyst merely because the title also mentions data analytics.
MANAGEMENT_MARKER = (
    r"\b(?:manager\w*|manazer\w*|projectmanager\w*|projektmanager\w*|"
    r"vedouc\w*|head|director\w*|redit\w*|"
    r"lead(?:er)?(?!\s+generation\b)|lidr\w*|supervisor|chief|ceo|cfo|cto|"
    r"cio|coo|cdo|ciso|cmo|vp|project manag)\b"
)
NON_MANAGEMENT_TITLE = rf"^(?!.*{MANAGEMENT_MARKER})"

OCCUPATION_PRECEDENCE_RULES: tuple[TitleRule, ...] = (
    # Leadership always wins when a title explicitly combines management with
    # a hands-on occupation, such as "Team Leader / Programmer".
    TitleRule("Manager", (MANAGEMENT_MARKER,)),
    TitleRule("Controller", (r"\bcontroller\w*\b", r"\bkontroler\w*\b")),
    TitleRule("Sales Representative", (r"\bbusiness developer\w*\b",)),
    TitleRule(
        "Java Developer",
        (
            r"\bjava\b.*\bdeveloper\w*\b",
            r"\bdeveloper\w*\b.*\bjava\b",
        ),
    ),
    TitleRule(
        "Customer Service Representative",
        (
            r"\bkonzultant\w*\b.*\bzakaznick\w* link\w*\b",
            r"\bzakaznick\w* link\w*\b.*\bprichozi hovor\w*\b",
            r"\bspecialist\w*\b.*\bzakaznick\w* centr\w*\b",
        ),
    ),
    TitleRule(
        "Support Specialist",
        (
            r"\bspecialist\w*\b.*\b(?:klientsk\w*|customer|people) podpor\w*\b",
            r"\b(?:client|customer|people) support specialist\w*\b",
        ),
    ),
    TitleRule(
        "Tester",
        (
            NON_MANAGEMENT_TITLE
            + r"(?!.*\b(?:engineer\w*|inzenyr\w*|technician\w*|technik\w*|"
            + r"developer\w*|vyvojar\w*|programator\w*|programmer\w*)\b)"
            + r".*\btester\w*\b",
        ),
    ),
    TitleRule(
        "Planner",
        (
            NON_MANAGEMENT_TITLE
            + r"(?!.*\b(?:analyst\w*|analytik\w*)\b)"
            + r".*\bplanner\w*\b",
        ),
    ),
    TitleRule(
        "Coordinator",
        (
            r"\bproject support coordinator\w*\b",
            r"\bsecurity\b.*\bcoordinator\w*\b",
        ),
    ),
    TitleRule(
        "Administrative Assistant",
        (
            r"\b(?:assistant\w*|asistent\w*)\b.*\bdevelopersk\w*\b",
        ),
    ),
    TitleRule(
        "Teacher/Trainer",
        (
            NON_MANAGEMENT_TITLE
            + r".*\b(?:teacher\w*|trainer\w*|ucitel\w*|lektor\w*)\b",
        ),
    ),
    TitleRule(
        "Administrative Assistant",
        (
            r"\badministrativni podpor\w*\b",
            r"\bback office\b",
            r"\bpodpor\w* controlling\w*\b",
        ),
    ),
    TitleRule("Administrator", (r"\bobchodni administrativ\w*\b",)),
    TitleRule(
        "Sales Representative",
        (r"\bobchodnik\w*\b.*\bporadce\w*\b",),
    ),
    TitleRule("HR Specialist", (r"\bhris\b.*\bpartner\b",)),
    TitleRule(
        "Specialist",
        (
            r"\bmetodik\w*\b",
            r"\bfinance and accounting specialist\w*\b",
            r"\bfinancni specialista\w*\b",
            r"\bfinancial specialist\w*\b",
        ),
    ),
    TitleRule("Developer", (r"\bprogramator\w*\b", r"\bprogrammer\w*\b")),
)


# Root classification is intentionally occupation-led. These signals describe
# what the person *is* (analyst, coordinator, developer), not merely the domain
# in which they work (sales, security, cloud, accounting). All candidates are
# collected before a winner is selected. A higher score is reserved for clear
# semantic phrases and leadership; ordinary occupational nouns share a score,
# so their left-to-right order resolves genuinely compound hands-on titles.
ROOT_SIGNAL_RULES: tuple[RootSignal, ...] = (
    RootSignal("Manager", "explicit leadership", 1000, (MANAGEMENT_MARKER,)),
    RootSignal(
        "Sales Representative",
        "business developer",
        930,
        (r"\bbusiness developer\w*\b",),
    ),
    RootSignal(
        "Customer Service Representative",
        "customer-centre occupation",
        920,
        (
            r"\b(?:call centr\w*|zakaznick\w* centr\w*)\b",
            r"\b(?:customer care|customer service)\b",
            r"\bcustomer support\b(?!\s+engineer\b)",
            r"\bzakaznick\w* pec\w*\b",
            r"\bzakaznick\w* podpor\w*\b",
            r"\bklientsk\w* servis\w*\b",
            r"\bzakaznick\w* link\w*\b",
        ),
    ),
    RootSignal(
        "Support Specialist",
        "explicit support occupation",
        910,
        (
            r"\b(?:client|customer|people|technical|it|application|data|sw) support specialist\w*\b",
            r"\bsupport specialist\w*\b",
            r"\btechnical support\b(?!\s+engineer\b)",
            r"\b(?:help ?desk|service ?desk) specialist\w*\b",
            r"\bspecialist\w*\b.*\b(?:klientsk\w*|customer|people) podpor\w*\b",
            r"\bspecialist\w*\b.*\b(?:technick\w*|it|sw) podpor\w*\b",
            r"\bspecialist\w*\b.*\bpodpor\w*\b",
            r"\b(?:technick\w*|it|sw) podpor\w*\b",
            r"\badministrativ\w*\b.*\bsupport\b",
        ),
    ),
    RootSignal(
        "Assistant",
        "administrative/back-office support",
        900,
        (
            r"\badministrativni podpor\w*\b",
            r"\bback office specialist\w*\b",
            r"\bspecialist\w*\b.*\bback office\b",
            r"\bpodpor\w* controlling\w*\b",
        ),
    ),
    RootSignal(
        "Sales Representative",
        "explicit sales occupation",
        890,
        (
            r"\bsales representatives?\b",
            r"\bsales rep\b",
            r"\bsales development representatives?\b",
            r"\bbusiness development representatives?\b",
            r"\b(?:sales|business development|presales) specialist\w*\b",
            r"\baccount executive\b",
            r"\bobchodni zastup\w*\b",
            r"\bakvizicni obchodnik\w*\b",
            r"\bobchodnik\w*\b.*\bporadce\w*\b",
            r"\bsales intern\b",
            r"\bgtm operator\b.*\bsales development\b",
            r"\bbdr\b",
            r"\bfundraiser\w*\b",
        ),
    ),
    RootSignal(
        "Manager",
        "project/product executive",
        880,
        (r"\bproject executive\b", r"\bproduktak\b"),
    ),
    RootSignal(
        "Planner",
        "explicit production/supply planner",
        860,
        (
            r"\b(?:supply chain|demand|supply|production|manufacturing) planner\w*\b",
            r"\bplanner\w*\b.*\b(?:supply chain|production|manufacturing)\b",
            r"\bplanovac\w*\b.*\bvyrob\w*\b",
            r"\bplanovan\w* vyrob\w*\b",
        ),
    ),
    RootSignal(
        "Designer",
        "fashion/product-design occupation",
        850,
        (
            r"\b(?:fashion|garment|apparel|clothing|textile|footwear) developer\w*\b",
            r"\bdeveloper\w*\b.*\b(?:fashion|garment|apparel|clothing|textile|footwear)\b",
        ),
    ),
    RootSignal(
        "Recruiter",
        "recruitment occupation",
        880,
        (
            r"\btalent acquisition\b",
            r"\bspecialist\w*\b.*\bnabor\w*\b",
            r"\bnabor\w*\b.*\bspecialist\w*\b",
        ),
    ),
    RootSignal(
        "Accountant",
        "accounting expert",
        870,
        (r"\baccounting expert\b", r"\bexpert\w*\b.*\bucetnictvi\b"),
    ),
    RootSignal(
        "Specialist",
        "legal occupation",
        870,
        (r"\blegal (?:advisor|counsel)\b", r"\bpravnik\w*\b", r"\badvokat\w*\b"),
    ),
    RootSignal(
        "Specialist",
        "explicit application specialist",
        870,
        (r"\baplikacni specialist\w*\b.*\bspravce\b",),
    ),
    RootSignal(
        "Analyst",
        "specific systems/process analyst",
        860,
        (
            r"\b(?:system\w*|proces\w*)\b.*\banalyt\w*\b",
            r"\banalyt\w*\b.*\b(?:system\w*|proces\w*)\b",
        ),
    ),
    RootSignal(
        "Architect",
        "explicit architect",
        850,
        (r"\barchitect\b", r"\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b"),
    ),
    RootSignal("Controller", "explicit controller", 840, (r"\bcontroller\w*\b", r"\bkontroler\w*\b")),
    RootSignal(
        "Controller",
        "Czech financial controller",
        840,
        (r"\bfinancni kontrolor\w*\b",),
    ),
    RootSignal("Accountant", "explicit accountant", 835, (r"\baccountant\w*\b", r"\bucetni\b", r"\bbookkeeper\w*\b")),
    RootSignal("Scientist", "explicit scientist", 830, (r"\bscientist\w*\b", r"\bvedec\b", r"\bvedkyne\b")),
    RootSignal("Researcher", "explicit researcher", 825, (r"\bresearcher\w*\b", r"\bresearch fellow\b", r"\bvyzkumnik\w*\b", r"\bvyzkumnice\b", r"\bvyzkumn\w* pracovnik\w*\b", r"\bpostdoc\w*\b")),
    RootSignal("Auditor", "explicit auditor", 820, (r"\bauditor\w*\b",)),
    RootSignal("Recruiter", "explicit recruiter", 815, (r"\brecruiter\w*\b", r"\bpersonalista\w*\b")),
    RootSignal("Tester", "test analyst", 840, (r"\btest analyt\w*\b",)),
    RootSignal("Tester", "explicit tester", 800, (r"\btester\w*\b",)),
    RootSignal("Designer", "explicit designer", 800, (r"\bdesigner\w*\b", r"\bgrafik\w*\b")),
    RootSignal(
        "Developer",
        "explicit developer",
        800,
        (
            r"\bdevelopers?\b(?!\s+experience\b)",
            r"\bvyvojar\w*\b",
            r"\bprogramator\w*\b",
            r"\bprogrammer\w*\b",
            r"\bkoder\w*\b",
        ),
    ),
    RootSignal("Engineer", "explicit engineer", 800, (r"\bengineers?\b", r"\binzenyr\w*\b", r"\binzernyr\w*\b")),
    RootSignal("Analyst", "explicit analyst", 800, (r"\banalyst\w*\b", r"\banalytik\w*\b", r"\banalyticka\w*\b")),
    RootSignal("Administrator", "explicit administrator", 800, (r"\badministrator\w*\b", r"\bspravce\b", r"\badmin\b", r"\breferent\w*\b")),
    RootSignal(
        "Administrator",
        "explicit administration function",
        810,
        (
            r"\b(?:financni|financial|finance|personalni|personnel|hr|office|business|sales|obchodni)\b"
            r"(?:\s+(?:a|and))?(?:\s+(?:financni|financial|finance|personalni|personnel|hr|office|business|sales|obchodni))?"
            r"\s+administrativ\w*\b",
            r"\badministrativ\w*\b.*\b(?:financni|financial|finance|personalni|personnel|hr)\b",
        ),
    ),
    RootSignal("Consultant", "explicit consultant", 800, (r"\bconsultant\w*\b", r"\bkonzultant\w*\b", r"\bkonsultant\w*\b", r"\bporadce\w*\b")),
    RootSignal("Sales Representative", "explicit salesperson", 800, (r"\bobchodnik\w*\b", r"\bprodejce\w*\b", r"\bzastupce\w*\b", r"\brepresentative\w*\b")),
    RootSignal("Coordinator", "explicit coordinator", 800, (r"\bco ordinator\w*\b", r"\bcoordinator\w*\b", r"\bkoordinator\w*\b")),
    RootSignal("Technician", "explicit technician", 800, (r"\btechnician\w*\b", r"\btechnik\w*\b", r"\btechnolog(?:ka|ove|a|u|em|i)?\b", r"\bmechanik\w*\b")),
    RootSignal("Planner", "explicit planner", 800, (r"\bplanner\w*\b", r"\bscheduler\w*\b", r"\bplanovac\w*\b", r"\brozpoctar\w*\b", r"\bpripravar\w*\b")),
    RootSignal("Assistant", "explicit assistant", 800, (r"\bassistant\w*\b", r"\basistant\w*\b", r"\basistent\w*\b", r"\bsekretar\w*\b", r"\brecepcni\b", r"\breceptionist\w*\b")),
    RootSignal("Teacher/Trainer", "explicit teacher/trainer", 800, (r"\bteacher\w*\b", r"\btrainer\w*\b", r"\btrener\w*\b", r"\bcoach\w*\b", r"\bkouc\w*\b", r"\bucitel\w*\b", r"\blektor\w*\b", r"\bvyucujic\w*\b")),
    RootSignal("Healthcare Professional", "explicit healthcare occupation", 800, (r"\bdoctor\w*\b", r"\bnurse\w*\b", r"\blekar\w*\b", r"\bsestra\b", r"\bfarmaceut\w*\b", r"\bpharmacist\w*\b", r"\boptometrist\w*\b")),
    RootSignal("Operator", "explicit operator", 800, (r"\boperator\w*\b", r"\boperater\w*\b", r"\bobsluha\b")),
    RootSignal("Driver", "explicit driver", 800, (r"\bdriver\w*\b", r"\bridic(?:ka|e|u|em|um|ove)?\b", r"\bkuryr\w*\b")),
    RootSignal("Warehouse Worker", "explicit warehouse worker", 800, (r"\bwarehouse worker\w*\b", r"\bskladnik\w*\b")),
    RootSignal("Production Worker", "explicit production worker", 800, (r"\bproduction worker\w*\b", r"\bvyrobni pracovnik\w*\b", r"\bpracovnik\w* vyroby\b")),
    RootSignal("Intern/Trainee", "explicit intern/trainee", 780, (r"\bintern(?:ship)?\b", r"\btrainee\b", r"\bstazist\w*\b")),
    RootSignal("Agent", "explicit agent", 780, (r"\bagent(?:ka|i|a|u|em|ovi|s)?\b", r"\boperative\w*\b")),
    RootSignal("Officer", "explicit officer", 780, (r"\bofficer\w*\b",)),
    RootSignal("Clerk", "explicit clerk", 780, (r"\bclerk\w*\b",)),
    RootSignal(
        "Customer Service Representative",
        "customer-care activity",
        760,
        (
            r"\btelefonick\w* pec\w*\b.*\bzakaznik\w*\b",
            r"\bzpracovani reklamac\w*\b",
        ),
    ),
    RootSignal(
        "Scientist",
        "statistical modeler",
        760,
        (r"\bstatistical model(?:er|ler)\w*\b",),
    ),
    RootSignal(
        "Technician",
        "quality inspector",
        760,
        (r"\bkontrolor\w* kvality\b",),
    ),
    RootSignal(
        "Specialist",
        "additional occupation noun",
        720,
        (
            r"\bassociate\w*\b",
            r"\bbusiness partner\w*\b",
            r"\bdevelopment partner\w*\b",
            r"\bstrategist\w*\b",
            r"\bintegrator\w*\b",
            r"\btranscriber\w*\b",
            r"\bbookmaker\w*\b",
            r"\bbanker\w*\b",
            r"\benergetik\w*\b",
            r"\bodhadce\w*\b",
            r"\bgestor\w*\b",
            r"\bvisual commercial\b",
            r"\bbusiness builder\b",
        ),
    ),
    # An explicit "specialist" is an occupation and must outrank a bare
    # internship-level token. It remains below engineer/developer/analyst so a
    # compound title such as "Automation Engineer | Specialist" follows the
    # more concrete profession.
    RootSignal("Specialist", "explicit specialist", 790, (r"\bspecialist\w*\b", r"\bspecialista\w*\b", r"\bodbornik\w*\b", r"\bodborn\w* pracovnik\w*\b", r"\bexpert\w*\b")),
)


# Rule order matters. Specific technical positions precede their broad parent
# occupations; management terms precede specialist/analyst terms so titles such
# as "Analytics Manager" correctly become "Manager".
TITLE_RULES: tuple[TitleRule, ...] = (
    TitleRule(
        "Manager",
        (
            r"\bmanager\w*\b",
            r"\bmanazer\w*\b",
            r"\bvedouc\w*\b",
            r"\bhead of\b",
            r"\bdirector(?:ka)?\b",
            r"\bredit(?:el|elka)\b",
            r"\bteam lead(?:er)?\b",
            r"\bteamleader\b",
            r"\blead\b",
            r"\bleader\b",
            r"\blidr\w*\b",
            r"\bsupervisor\b",
            r"\bprojektovy vedouci\b",
            r"\bprojektak\b",
            r"\bmistr\b",
            r"\b(?:product|project|functional) owner\b",
            r"\bchief\b",
            r"\b(?:ceo|cfo|cto|cio|coo|cdo|ciso|vp)\b",
        ),
    ),
    TitleRule(
        "AI Architect",
        (r"\bai\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b", r"\bai\b.*\barchitect\b", r"\barchitect\b.*\bai\b"),
    ),
    TitleRule(
        "Data Architect",
        (r"\bdata\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b", r"\bdata\b.*\barchitect\b", r"\barchitect\b.*\bdata\b"),
    ),
    TitleRule(
        "Cloud Architect",
        (r"\bcloud\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b", r"\bcloud\b.*\barchitect\b", r"\barchitect\b.*\bcloud\b"),
    ),
    TitleRule(
        "Solution Architect",
        (
            r"\bsolution\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b",
            r"\bsolution\b.*\barchitect\b",
            r"\breseni\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b",
        ),
    ),
    TitleRule(
        "Enterprise Architect",
        (r"\benterprise\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b", r"\benterprise\b.*\barchitect\b"),
    ),
    TitleRule(
        "Software Architect",
        (
            r"\bsoftware\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b",
            r"\bsoftware\b.*\barchitect\b",
            r"\bsystem(?:ovy|u)?\b.*\barchitekt(?:ka|ky|ku|em|a|i|u|ovi)?\b",
            r"\bsystems?\b.*\barchitect\b",
        ),
    ),
    TitleRule("Architect", (r"\barchitect\b", r"\barchitekt(?:ka)?\b")),
    TitleRule(
        "Data Analyst",
        (
            r"\bdata\b.*\banalyst",
            r"\bdata\b.*\banalyt",
            r"\bdatov(?:y|a|e|ych)\b.*\banalyt",
            r"\banalyst\b.*\bdata\b",
            r"\banalyt\w*\b.*\bdat",
        ),
    ),
    TitleRule(
        "Business Analyst",
        (
            r"\bbusiness\b.*\banalyst",
            r"\bproces(?:s|ni)\w*\b.*\banalyt",
            r"\banalyt\w*\b.*\bproces",
            r"\bfunctional analyst\b",
        ),
    ),
    TitleRule(
        "Financial Analyst",
        (
            r"\bfinanc\w*\b.*\banalyst",
            r"\bfinanc\w*\b.*\banalyt",
            r"\banalyst\b.*\bfinanc",
            r"\banalyt\w*\b.*\bfinanc",
            r"\binvestment analyst\b",
            r"\binvesticni analyt",
        ),
    ),
    TitleRule(
        "Risk Analyst",
        (
            r"\brisk\b.*\banalyst",
            r"\brizik\w*\b.*\banalyt",
            r"\banalyst\b.*\brisk\b",
            r"\banalyt\w*\b.*\brizik",
        ),
    ),
    TitleRule(
        "Security Analyst",
        (
            r"\b(?:cyber|security|soc)\b.*\banalyst",
            r"\bbezpecnost\w*\b.*\banalyt",
            r"\banalyt\w*\b.*\b(?:cyber|security|soc|bezpecnost)",
        ),
    ),
    TitleRule(
        "Marketing Analyst",
        (
            r"\bmarketing\w*\b.*\banalyst",
            r"\bmarketing\w*\b.*\banalyt",
            r"\banalyst\b.*\bmarketing\b",
            r"\banalyt\w*\b.*\bmarketing",
            r"\bcustomer insights? analyst\b",
        ),
    ),
    TitleRule(
        "Product Analyst",
        (r"\bproduct\w*\b.*\banalyst", r"\bprodukt\w*\b.*\banalyt"),
    ),
    TitleRule(
        "BI Analyst",
        (
            r"\bbi\b.*\banalyst",
            r"\bbusiness intelligence\b.*\banalyst",
            r"\breporting\b.*\banalyst",
        ),
    ),
    TitleRule(
        "IT Analyst",
        (
            r"\bit\b.*\banalyst",
            r"\bit\b.*\banalyt",
            r"\bict\b.*\banalyst",
            r"\bsystem\w*\b.*\banalyt",
            r"\banalyt\w*\b.*\bsystem",
        ),
    ),
    TitleRule("Analyst", (r"\banalyst(?:ka)?\b", r"\banalytik\b", r"\banalyticka\b")),
    TitleRule(
        "Data Scientist",
        (r"\bdata scientist\b", r"\bdatov\w* ved(?:ec|kyne)\b"),
    ),
    TitleRule(
        "Researcher",
        (
            r"\bresearcher\b",
            r"\bresearch fellow\b",
            r"\bvyzkumn(?:ik|ice|y pracovnik)\b",
            r"\bvedeck\w* pracovnik",
            r"\bpostdoc(?:toral)?\b",
        ),
    ),
    TitleRule(
        "Scientist",
        (r"\bscientist\b", r"\bvedec\b", r"\bvedkyne\b", r"\bchemik(?:a|cka)?\b"),
    ),
    TitleRule(
        "Machine Learning Engineer",
        (
            r"\bmachine learning\b.*\bengineer",
            r"\bml\b.*\bengineer",
            r"\bai ml engineer\b",
            r"\bengineer\b.*\bmachine learning\b",
        ),
    ),
    TitleRule(
        "AI Engineer",
        (
            r"\bai\b.*\bengineer",
            r"\bartificial intelligence\b.*\bengineer",
            r"\bllm\b.*\bengineer",
            r"\bengineer\b.*\bai\b",
        ),
    ),
    TitleRule(
        "Data Engineer",
        (
            r"\bdata\b.*\bengineer",
            r"\bdatov\w*\b.*\binzenyr",
            r"\bengineer\b.*\bdata\b",
        ),
    ),
    TitleRule(
        "Analytics Engineer",
        (r"\banalytics\b.*\bengineer", r"\bengineer\b.*\banalytics\b"),
    ),
    TitleRule(
        "DevOps Engineer",
        (
            r"\bdevops\b",
            r"\bsite reliability engineer\b",
            r"\bsre engineer\b",
            r"\breliability engineer\b",
        ),
    ),
    TitleRule(
        "Security Engineer",
        (
            r"\b(?:cyber|security)\b.*\bengineer",
            r"\bbezpecnost\w*\b.*\binzenyr",
            r"\bengineer\b.*\b(?:cyber|security)\b",
        ),
    ),
    TitleRule(
        "Cloud Engineer",
        (r"\bcloud\b.*\bengineer", r"\bengineer\b.*\bcloud\b"),
    ),
    TitleRule(
        "Platform Engineer",
        (r"\bplatform\b.*\bengineer", r"\bengineer\b.*\bplatform\b"),
    ),
    TitleRule(
        "QA Engineer",
        (
            r"\bqa\b.*\bengineer",
            r"\bquality assurance\b.*\bengineer",
            r"\btest automation\b.*\bengineer",
            r"\bautomation test\w*\b",
            r"\bqa\b.*\btest\w*\b",
        ),
    ),
    TitleRule(
        "Network Engineer",
        (
            r"\bnetwork\b.*\bengineer",
            r"\bsitov\w*\b.*\binzenyr",
            r"\binzenyr\w*\b.*\bsit",
        ),
    ),
    TitleRule(
        "Software Engineer",
        (
            r"\bsoftware\b.*\bengineer",
            r"\bsoftware development engineer\b",
            r"\bsw engineer\b",
        ),
    ),
    TitleRule(
        "Engineer",
        (r"\bengineer\b", r"\bengineering\b", r"\binzenyr(?:ka)?\b"),
    ),
    TitleRule(
        "Full Stack Developer",
        (
            r"\bfull stack\b",
            r"\bfullstack\b",
            r"\bfull stackovy\b",
        ),
    ),
    TitleRule(
        "Backend Developer",
        (r"\bback end\b", r"\bbackend\b", r"\bserver side\b"),
    ),
    TitleRule(
        "Frontend Developer",
        (r"\bfront end\b", r"\bfrontend\b", r"\bweb ui developer\b"),
    ),
    TitleRule(
        "Mobile Developer",
        (
            r"\bmobile\b.*\bdeveloper\w*",
            r"\bandroid\b.*\bdeveloper\w*",
            r"\bios\b.*\bdeveloper\w*",
            r"\bflutter\b.*\bdeveloper\w*",
        ),
    ),
    TitleRule(".NET Developer", (r"\bnet developer\w*\b", r"\bc#\b.*\bdeveloper\w*")),
    TitleRule("Java Developer", (r"\bjava\b.*\bdeveloper\w*", r"\bjava\b.*\bvyvojar")),
    TitleRule("Python Developer", (r"\bpython\b.*\bdeveloper\w*", r"\bpython\b.*\bvyvojar")),
    TitleRule("PHP Developer", (r"\bphp\b.*\bdeveloper\w*", r"\bphp\b.*\bvyvojar")),
    TitleRule(
        "C/C++ Developer",
        (r"\bc\+\+.*\bdeveloper\w*", r"\bc\b.*\bc\+\+.*\bvyvojar"),
    ),
    TitleRule("ABAP Developer", (r"\babap\b.*\bdeveloper\w*", r"\babap\b.*\bvyvojar")),
    TitleRule("SQL Developer", (r"\bsql\b.*\bdeveloper\w*", r"\bsql\b.*\bvyvojar")),
    TitleRule("Salesforce Developer", (r"\bsalesforce\b.*\bdeveloper\w*",)),
    TitleRule(
        "Software Developer",
        (
            r"\bsoftware\b.*\bdeveloper\w*",
            r"\bsoftware\b.*\bvyvojar",
            r"\bsw\b.*\bvyvojar",
        ),
    ),
    TitleRule(
        "Developer",
        (
            r"\bdeveloper\w*\b",
            r"\bvyvojar(?:ka)?\b",
            r"\bprogrammer\b",
            r"\bprogramator(?:ka)?\b",
            r"\bkoder(?:ka)?\b",
            r"\b(?:ror|react|sharepoint) dev\b",
            r"\bruby on rails\b",
            r"\bjava\b.*\bucen\b",
            r"\bruby\b.*\baplikac",
            r"\bnaprogram\w*\b",
        ),
    ),
    TitleRule(
        "Database Administrator",
        (
            r"\bdatabase administrator\b",
            r"\bdatabazov\w* administrator",
            r"\bdba\b",
        ),
    ),
    TitleRule(
        "Systems Administrator",
        (
            r"\bsystems? administrator\b",
            r"\bsystemov\w* administrator",
            r"\bsysadmin\b",
            r"\bspravce\b.*\b(?:system|server|site|infrastruktur)",
        ),
    ),
    TitleRule(
        "IT Administrator",
        (
            r"\bit administrator\b",
            r"\bit administrat",
            r"\badministrator\b.*\bit\b",
            r"\bspravce it\b",
        ),
    ),
    TitleRule(
        "Administrator",
        (
            r"\badministrator(?:ka)?\b",
            r"\badministrativni pracov",
            r"\breferent(?:ka)?\b",
            r"\badmin\b",
            r"\badministrac\w*\b",
        ),
    ),
    TitleRule(
        "SAP Consultant",
        (r"\bsap\b.*\bconsultant", r"\bsap\b.*\bkonzultant"),
    ),
    TitleRule(
        "IT Consultant",
        (
            r"\bit\b.*\bconsultant",
            r"\bit\b.*\bkonzultant",
            r"\btechnology consultant\b",
        ),
    ),
    TitleRule("Consultant", (r"\bconsultant\b", r"\bkonzultant(?:ka)?\b", r"\bporadce\b")),
    TitleRule(
        "Software Tester",
        (
            r"\bsoftware tester\b",
            r"\bsw tester\b",
            r"\bqa tester\b",
            r"\btest analyt",
            r"\bmanual tester\b",
        ),
    ),
    TitleRule("Tester", (r"\btester(?:ka)?\b", r"\btestovaci\b", r"\btestovani\b")),
    TitleRule("UX/UI Designer", (r"\bux\b", r"\bui designer\b", r"\bproduct designer\b")),
    TitleRule(
        "Designer",
        (r"\bdesigner\w*\b", r"\bdesign(?:er)?\b", r"\bgrafik\w*\b", r"\bgraphic\b"),
    ),
    TitleRule(
        "IT Support Specialist",
        (
            r"\bit\b.*\bsupport\w*",
            r"\bit\b.*\bpodpor",
            r"\bhelp ?desk\b",
            r"\bservice ?desk\b",
            r"\bapplication support\w*\b",
            r"\btechnicka podpora\b",
        ),
    ),
    TitleRule(
        "Customer Service Representative",
        (
            r"\bcustomer (?:care|service)\b",
            r"\bcustomer support\b",
            r"\bzakaznick\w* (?:servis|podpor|pece)\w*\b",
            r"\bklientsk\w* (?:servis|podpor|pece)\w*\b",
            r"\bcall cent(?:er|re)\b",
        ),
    ),
    TitleRule("Support Specialist", (r"\bsupport\w*\b", r"\bpodpor\w*\b")),
    TitleRule(
        "Accountant",
        (
            r"\baccountant\b",
            r"\baccounting\b",
            r"\bucetni\b",
            r"\bucetnictvi\b",
            r"\bbookkeeper\b",
        ),
    ),
    TitleRule("Controller", (r"\bcontroller\b", r"\bcontrolling\b", r"\bkontroler(?:ka)?\b")),
    TitleRule("Auditor", (r"\bauditor(?:ka)?\b", r"\baudit\b", r"\bauditu\b")),
    TitleRule(
        "Recruiter",
        (
            r"\brecruiter\b",
            r"\brecruitment\b",
            r"\btalent acquisition\b",
            r"\bnabor\w*\b",
            r"\bpersonalista\b",
            r"\brecruiter\w*\b",
        ),
    ),
    TitleRule(
        "HR Specialist",
        (
            r"\bhr\b",
            r"\bhuman resources\b",
            r"\blidsk\w* zdroj",
            r"\bpayroll\b",
            r"\bmzdov\w*\b",
            r"\bemployee relations\b",
            r"\bcompensation\b",
            r"\bbenefits partner\b",
        ),
    ),
    TitleRule(
        "Marketing Specialist",
        (
            r"\bmarketing\b",
            r"\bmarketer\b",
            r"\bbrand\b",
            r"\bsocial media\b",
            r"\bcontent\b",
            r"\bseo\b",
            r"\bppc\b",
            r"\bcopywriter\b",
            r"\bkomunikac\w*\b",
            r"\bproducer\b",
            r"\bvideomaker\b",
            r"\bvideo production\b",
            r"\beditor\w*\b",
            r"\bredaktor\w*\b",
            r"\bmerchandis\w*\b",
            r"\bprista\b",
        ),
    ),
    TitleRule(
        "Sales Representative",
        (
            r"\bsales\b",
            r"\bobchodnik(?:ce|yn)?\b",
            r"\bobchodni zastup",
            r"\bprodejce\b",
            r"\bakvizicni\b",
            r"\bbusiness development\b",
            r"\bsalesman\b",
            r"\bpresales\b",
            r"\baccount executive\b",
        ),
    ),
    TitleRule(
        "Procurement Specialist",
        (
            r"\bprocurement\b",
            r"\bpurchas\w*\b",
            r"\bbuyer\b",
            r"\bnakup\w*\b",
        ),
    ),
    TitleRule(
        "Logistics Specialist",
        (
            r"\blogistic\w*\b",
            r"\blogistik\w*\b",
            r"\bsupply chain\b",
            r"\btransport\w*\b",
            r"\bdisponent\w*\b",
            r"\bdispecer\w*\b",
            r"\bdispatcher\b",
        ),
    ),
    TitleRule(
        "Finance Specialist",
        (
            r"\bfinanc\w*\b",
            r"\btreasury\b",
            r"\binvestic\w*\b",
            r"\bbanking\b",
            r"\bbanker\b",
            r"\bbankovni\b",
            r"\bekonom\w*\b",
            r"\bactuar\w*\b",
            r"\bpojistn\w*.*\bmatematik",
            r"\btrader\b",
            r"\bbroker\b",
            r"\bmakler\w*\b",
            r"\bcash collector\b",
            r"\baccounts receivable processor\b",
        ),
    ),
    TitleRule(
        "Data Specialist",
        (
            r"\bdata steward\b",
            r"\bdata governance\b",
            r"\bmaster data\b",
            r"\bbusiness intelligence\b",
        ),
    ),
    TitleRule(
        "Security Specialist",
        (
            r"\bcyber\w*\b",
            r"\bsecurity\b",
            r"\bbezpecnost\w*\b",
            r"\binformation security\b",
        ),
    ),
    TitleRule(
        "Compliance Specialist",
        (r"\bcompliance\b", r"\baml\b", r"\bkyc\b", r"\bregulatory\b"),
    ),
    TitleRule(
        "IT Specialist",
        (
            r"\bit\b",
            r"\bict\b",
            r"\binformacni technolog",
            r"\binformacni system",
        ),
    ),
    TitleRule(
        "Specialist",
        (
            r"\bspecialist\w*\b",
            r"\bspecialista\b",
            r"\bodbornik(?:ice)?\b",
            r"\bexpert(?:ka)?\b",
        ),
    ),
    TitleRule("Project Coordinator", (r"\bproject coordinator\b", r"\bprojektov\w* koordinator")),
    TitleRule(
        "Coordinator",
        (
            r"\bco ordinator\b",
            r"\bcoordinator\b",
            r"\bcoordination\b",
            r"\bkoordinator(?:ka)?\b",
        ),
    ),
    TitleRule(
        "Technician",
        (
            r"\btechnician\b",
            r"\btechnik(?:a|cka)?\b",
            r"\bmechanik(?:a)?\b",
            r"\belektrikar\b",
            r"\bservisni pracovnik\b",
            r"\btechnolog\w*\b",
            r"\bkonstrukter\w*\b",
            r"\bprojektant\w*\b",
            r"\bmetrolog\w*\b",
            r"\bgeodet\w*\b",
        ),
    ),
    TitleRule(
        "Planner",
        (r"\bplanner\b", r"\bplanovac\b", r"\bplanovani\b", r"\brozpoctar\w*\b"),
    ),
    TitleRule(
        "Administrative Assistant",
        (
            r"\badministrativni asist",
            r"\badministrative assistant\b",
            r"\boffice assistant\b",
            r"\bback office\b",
        ),
    ),
    TitleRule("Assistant", (r"\bassistant\b", r"\basistent(?:ka)?\b", r"\bsekretar(?:ka)?\b")),
    TitleRule("Administrative Assistant", (r"\breceptionist\b", r"\brecepcni\b")),
    TitleRule("Legal Specialist", (r"\blawyer\b", r"\blegal\b", r"\bpravnik(?:ka)?\b", r"\badvokat\w*\b")),
    TitleRule("Teacher/Trainer", (r"\bteacher\b", r"\btrainer\b", r"\bucitel(?:ka)?\b", r"\blektor(?:ka)?\b")),
    TitleRule(
        "Healthcare Professional",
        (
            r"\bdoctor\b",
            r"\bnurse\b",
            r"\blekar(?:ka)?\b",
            r"\bsestra\b",
            r"\bfarmaceut\w*\b",
            r"\bpharmac\w*\b",
        ),
    ),
    TitleRule("Operator", (r"\boperator(?:ka)?\b", r"\boperater(?:ka)?\b", r"\bobsluha\b")),
    TitleRule("Driver", (r"\bdriver\b", r"\bridic(?:ka)?\b", r"\bkuryr(?:ka)?\b")),
    TitleRule(
        "Warehouse Worker",
        (r"\bwarehouse\b", r"\bskladnik(?:ce)?\b", r"\bskladov\w* pracov"),
    ),
    TitleRule(
        "Production Worker",
        (r"\bproduction worker\b", r"\bvyrobni pracov", r"\bpracovnik\w* vyroby\b", r"\bmontazni\b"),
    ),
    TitleRule(
        "Intern/Trainee",
        (r"\bintern\b", r"\binternship\b", r"\btrainee\b", r"\bstaz\w*\b"),
    ),
    TitleRule("Sales Representative", (r"\brepresentative\b", r"\bzastupce\b")),
    TitleRule("Agent", (r"\bagent(?:ka)?\b", r"\boperative\b")),
    TitleRule("Officer", (r"\bofficer\b",)),
    TitleRule("Clerk", (r"\bclerk\b", r"\bpracovnik(?:ce)?\b", r"\breferent(?:ka)?\b")),
)


# These groups deliberately define only the first, general occupation level.
# The rule labels remain more detailed so a later version can expose them in a
# separate Subtype column without rebuilding the classification logic.
ROOT_POSITION_GROUPS: dict[str, frozenset[str]] = {
    "Architect": frozenset(
        {
            "AI Architect",
            "Data Architect",
            "Cloud Architect",
            "Solution Architect",
            "Enterprise Architect",
            "Software Architect",
            "Architect",
        }
    ),
    "Analyst": frozenset(
        {
            "Data Analyst",
            "Business Analyst",
            "Financial Analyst",
            "Risk Analyst",
            "Security Analyst",
            "Marketing Analyst",
            "Product Analyst",
            "BI Analyst",
            "IT Analyst",
            "Analyst",
        }
    ),
    "Scientist": frozenset({"Data Scientist", "Scientist"}),
    "Engineer": frozenset(
        {
            "Machine Learning Engineer",
            "AI Engineer",
            "Data Engineer",
            "Analytics Engineer",
            "DevOps Engineer",
            "Security Engineer",
            "Cloud Engineer",
            "Platform Engineer",
            "QA Engineer",
            "Network Engineer",
            "Software Engineer",
            "Engineer",
        }
    ),
    "Developer": frozenset(
        {
            "Full Stack Developer",
            "Backend Developer",
            "Frontend Developer",
            "Mobile Developer",
            ".NET Developer",
            "Java Developer",
            "Python Developer",
            "PHP Developer",
            "C/C++ Developer",
            "ABAP Developer",
            "SQL Developer",
            "Salesforce Developer",
            "Software Developer",
            "Developer",
        }
    ),
    "Administrator": frozenset(
        {
            "Database Administrator",
            "Systems Administrator",
            "IT Administrator",
            "Administrator",
        }
    ),
    "Consultant": frozenset({"SAP Consultant", "IT Consultant", "Consultant"}),
    "Tester": frozenset({"Software Tester", "Tester"}),
    "Designer": frozenset({"UX/UI Designer", "Designer"}),
    "Support Specialist": frozenset(
        {"IT Support Specialist", "Support Specialist"}
    ),
    "Specialist": frozenset(
        {
            "HR Specialist",
            "Marketing Specialist",
            "Procurement Specialist",
            "Logistics Specialist",
            "Finance Specialist",
            "Data Specialist",
            "Security Specialist",
            "Compliance Specialist",
            "IT Specialist",
            "Legal Specialist",
            "Specialist",
        }
    ),
    "Coordinator": frozenset({"Project Coordinator", "Coordinator"}),
    "Assistant": frozenset({"Administrative Assistant", "Assistant"}),
}

ROOT_POSITION_BY_SUBTYPE = {
    subtype: root
    for root, subtypes in ROOT_POSITION_GROUPS.items()
    for subtype in subtypes
}

ROOT_TAXONOMY = frozenset(
    {
        "Manager",
        "Architect",
        "Analyst",
        "Scientist",
        "Researcher",
        "Engineer",
        "Developer",
        "Administrator",
        "Consultant",
        "Tester",
        "Designer",
        "Support Specialist",
        "Customer Service Representative",
        "Accountant",
        "Controller",
        "Auditor",
        "Recruiter",
        "Specialist",
        "Sales Representative",
        "Coordinator",
        "Technician",
        "Planner",
        "Assistant",
        "Teacher/Trainer",
        "Healthcare Professional",
        "Operator",
        "Driver",
        "Warehouse Worker",
        "Production Worker",
        "Intern/Trainee",
        "Agent",
        "Officer",
        "Clerk",
        "Other",
    }
)

# These are behavioral contracts, not one-off classification branches. They
# cover precedence, morphology, contextual false positives, and compound-role
# decisions that future edits must preserve.
REGRESSION_CASES: tuple[tuple[str, str], ...] = (
    ("Administrátor online reklamy", "Administrator"),
    ("Senior Payroll Administrator – Prague | Pharma | Hybrid", "Administrator"),
    ("Operátor/ka call centra - neomezené bonusy!", "Customer Service Representative"),
    ("Technical Support Representative", "Support Specialist"),
    ("Sales Representatives/Consultant - Energy Management System", "Sales Representative"),
    ("Specialista/ka klientské podpory - back office", "Support Specialist"),
    ("Master Data Co-Ordinator", "Coordinator"),
    ("Purchasing Coordinator (elektro) - Praha-západ", "Coordinator"),
    ("⚡️Obchodník po telefonu s duší poradce | neomezené provize | Praha", "Sales Representative"),
    ("Specialista zákaznického centra", "Customer Service Representative"),
    ("IT Core Process Specialist - Cloud & Architecture (f/m/d)", "Specialist"),
    ("Senior Java Developer - AI-Driven Engineer (m/f)", "Developer"),
    ("Team leader/Programátor — DEK a.s.", "Manager"),
    ("Supply Chain Import Planner (m/ž)", "Planner"),
    ("Finance & Accounting Specialist", "Specialist"),
    ("Security & Emergency Response Planning Coordinator", "Coordinator"),
    ("Business Developer - vývoj elektronických systémů", "Sales Representative"),
    ("Python programátor/ka pro DevOps tým", "Developer"),
    ("Podpora controllingu - 20 h týdně", "Assistant"),
    ("QA Tester s vizí: víc než jen klikání!", "Tester"),
    ("Test analytik / Tester (m/ž) - O2 CRM Services", "Tester"),
    ("Asistent/ka v developerské společnosti s přesahem do marketingu", "Assistant"),
    ("HRIS & Process Excellence Partner", "Specialist"),
    ("HLEDÁME LEKTORY Z BANKOVNÍ PRAXE", "Teacher/Trainer"),
    ("Sales Entry Point – CZ & SK (Sales Rep / Sales Operations Assistant)", "Sales Representative"),
    ("Network Application Developer and Tester", "Developer"),
    ("SW Engineer/Vývojář C++", "Engineer"),
    ("Senior Cloud Engineer/ Architect", "Architect"),
    ("AI Solution Architect / Analyst | Enterprise projekty", "Architect"),
    ("KONZULTANT – SYSTÉMOVÝ A PROCESNÍ ANALYTIK", "Analyst"),
    ("Developer Experience Engineer (DevEx)", "Engineer"),
    ("Aplikační specialist(k)a - správce aplikačního SW", "Specialist"),
    ("Investigativní specialista - interní bezpečnost", "Specialist"),
    ("Specialista vývoje - test management řídící jednotky", "Specialist"),
    ("IT Business Analyst - SW Engineering projekty", "Analyst"),
    ("Lead Generation Specialist/ka - B2B - Finance", "Specialist"),
    ("Application Support Engineer", "Engineer"),
    ("Customer Support Engineer", "Engineer"),
    # Root-level failures found in the 200-row specialization audit.
    ("Projektmanager standardizace logistických procesů (m/ž)", "Manager"),
    ("Compliance Trainee", "Intern/Trainee"),
    (
        "Asistent/ka plánování výroby - Junior Supply Chain Planner",
        "Planner",
    ),
    (
        "Fashion Developer/ka pro vlastní kolekce spodního prádla a plavek",
        "Designer",
    ),
    ("Finanční a personální administrativa", "Administrator"),
    # Guardrails around the generalized fixes above.
    ("Data Engineer Intern", "Engineer"),
    ("Procurement Specialist Trainee - part time", "Specialist"),
    ("Trainee - Finance (M/Ž)", "Intern/Trainee"),
    ("Trainee Marketing Specialist", "Specialist"),
    ("Senior Demand & Supply Planner / S&OP Lead", "Manager"),
    ("Trainee do finančního oddělení (CFO office)", "Intern/Trainee"),
    ("Executive Assistant to CEO", "Assistant"),
    ("Chief Data Officer", "Manager"),
    ("Finanční kontrolor/ka", "Controller"),
    ("IT vyučující - VŠ předměty v AJ", "Teacher/Trainer"),
    ("Odborný pracovník oddělení klimatologie", "Specialist"),
    (
        "B2B Marketing Specialist (hands-on role s ambicí vyrůst v CMO)",
        "Specialist",
    ),
)

if sum(map(len, ROOT_POSITION_GROUPS.values())) != len(ROOT_POSITION_BY_SUBTYPE):
    raise RuntimeError("A detailed position appears in more than one root group.")


def classify_subtype(original_title: str) -> str:
    """Map one original title to the internal detailed position."""

    normalized = normalize_text(original_title)
    if not normalized:
        return "Other"

    override = EXACT_OVERRIDES.get(normalized)
    if override:
        return override

    for rule in OCCUPATION_PRECEDENCE_RULES:
        if rule.matches(normalized):
            return rule.unified_position

    for rule in TITLE_RULES:
        if rule.matches(normalized):
            return rule.unified_position

    return "Other"


def is_contextual_management_reference(
    normalized_title: str,
    match: re.Match[str],
) -> bool:
    """Reject leadership words that name somebody else or an org unit.

    Job titles such as "Executive Assistant to CEO" and "Trainee - CFO
    office" contain a senior title, but the advertised worker is not the CEO
    or CFO. Keeping this check at the evidence layer prevents those contextual
    mentions from defeating an explicit assistant or trainee occupation.
    """

    left = normalized_title[max(0, match.start() - 45) : match.start()]
    right = normalized_title[match.end() : match.end() + 20]
    matched_term = match.group().strip()

    if re.search(
        r"\b(?:assistant|asistent\w*|support|podpor\w*|reporting)\s+"
        r"(?:to|for|pro)\s*$",
        left,
    ):
        return True
    if re.search(r"\b(?:grow\s+into|vyrust\s+v|path\s+to|cesta\s+k)\s*$", left):
        return True
    if re.search(r"\boffice\s+of\s*$", left):
        return True
    if matched_term in {"ceo", "cfo", "cto", "cio", "coo", "cdo", "ciso", "cmo", "vp"}:
        return bool(re.match(r"\s+office\b", right))
    return False


def collect_occupation_candidates(normalized_title: str) -> list[OccupationCandidate]:
    """Collect every explicit occupation signal in a normalized title."""

    candidates: list[OccupationCandidate] = []
    seen: set[tuple[str, str, int, int]] = set()

    for signal in ROOT_SIGNAL_RULES:
        for pattern in signal.patterns:
            for match in re.finditer(pattern, normalized_title):
                if (
                    signal.root == "Manager"
                    and signal.name == "explicit leadership"
                    and is_contextual_management_reference(normalized_title, match)
                ):
                    continue
                key = (signal.root, signal.name, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    OccupationCandidate(
                        root=signal.root,
                        signal=signal.name,
                        score=signal.score,
                        start=match.start(),
                        end=match.end(),
                    )
                )

    return candidates


def classify_title_explained(original_title: str) -> ClassificationResult:
    """Classify a title and retain enough evidence for an audit report."""

    normalized = normalize_text(original_title)
    if not normalized:
        return ClassificationResult("Other", "Other", 0.0, "blank", "")

    override = EXACT_OVERRIDES.get(normalized)
    if override:
        root = ROOT_POSITION_BY_SUBTYPE.get(override, override)
        return ClassificationResult(root, override, 1.0, "exact override", normalized)

    candidates = collect_occupation_candidates(normalized)
    legacy_subtype = classify_subtype(original_title)
    legacy_root = ROOT_POSITION_BY_SUBTYPE.get(legacy_subtype, legacy_subtype)

    if candidates:
        # Score expresses semantic strength. Position breaks equal-score ties,
        # which makes "Engineer / Developer" and "Developer - AI Engineer"
        # follow the first explicit occupation without special-case strings.
        ranked = sorted(
            candidates,
            key=lambda candidate: (-candidate.score, candidate.start, -(candidate.end - candidate.start)),
        )
        winner = ranked[0]

        alternative_roots: list[str] = []
        for candidate in ranked[1:]:
            if candidate.root != winner.root and candidate.root not in alternative_roots:
                alternative_roots.append(candidate.root)

        if winner.score >= 900:
            confidence = 0.99
        elif winner.score >= 830:
            confidence = 0.97
        elif winner.score >= 800:
            confidence = 0.94
        elif winner.score >= 780:
            confidence = 0.90
        else:
            confidence = 0.86

        runner_up = next(
            (candidate for candidate in ranked[1:] if candidate.root != winner.root),
            None,
        )
        if runner_up and winner.score - runner_up.score <= 20:
            confidence = max(0.70, confidence - 0.10)

        subtype = legacy_subtype if legacy_root == winner.root else winner.root
        return ClassificationResult(
            root=winner.root,
            subtype=subtype,
            confidence=confidence,
            method="occupation signal",
            signal=winner.signal,
            alternatives=tuple(alternative_roots),
        )

    if legacy_root == "Other":
        return ClassificationResult(
            "Other", "Other", 0.25, "unclassified", "no occupation or domain rule"
        )

    # Domain-only and less common titles retain the established detailed rules,
    # but receive lower confidence because no occupational head was detected.
    return ClassificationResult(
        root=legacy_root,
        subtype=legacy_subtype,
        confidence=0.68,
        method="domain fallback",
        signal=legacy_subtype,
    )


def classify_title(original_title: str) -> str:
    """Map one original title to its root/general occupation type."""

    return classify_title_explained(original_title).root


def validate_classifier() -> None:
    """Fail fast when taxonomy or regression contracts are broken."""

    unknown_signal_roots = sorted(
        {signal.root for signal in ROOT_SIGNAL_RULES} - ROOT_TAXONOMY
    )
    if unknown_signal_roots:
        raise ValueError(
            "Root signals reference undefined taxonomy labels: "
            + ", ".join(unknown_signal_roots)
        )

    unknown_subtypes = sorted(
        {
            rule.unified_position
            for rule in (*OCCUPATION_PRECEDENCE_RULES, *TITLE_RULES)
            if rule.unified_position not in ROOT_POSITION_BY_SUBTYPE
            and rule.unified_position not in ROOT_TAXONOMY
        }
    )
    if unknown_subtypes:
        raise ValueError(
            "Detailed rules reference unmapped labels: "
            + ", ".join(unknown_subtypes)
        )

    failures = [
        (title, expected, classify_title(title))
        for title, expected in REGRESSION_CASES
        if classify_title(title) != expected
    ]
    if failures:
        details = "; ".join(
            f"{title!r}: expected {expected}, got {actual}"
            for title, expected, actual in failures
        )
        raise ValueError(f"Classifier regression check failed: {details}")


def find_title_column(fieldnames: Iterable[str | None]) -> str | None:
    wanted = normalize_header(INPUT_COLUMN)
    return next((field for field in fieldnames if normalize_header(field) == wanted), None)


def read_unique_titles(input_file: Path) -> tuple[list[str], int]:
    """Read unique nonblank titles while preserving their first spelling."""

    if not input_file.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_file}")
    if not input_file.is_file():
        raise ValueError(f"Input path is not a file: {input_file}")

    unique_titles: dict[str, str] = {}
    blank_rows = 0

    with input_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"Input CSV has no header: {input_file}")

        title_column = find_title_column(reader.fieldnames)
        if title_column is None:
            raise ValueError(
                f'Input CSV is missing the required "{INPUT_COLUMN}" column: '
                f"{input_file}"
            )

        for row in reader:
            title = " ".join((row.get(title_column) or "").split())
            if not title:
                blank_rows += 1
                continue
            unique_titles.setdefault(title.casefold(), title)

    return sorted(unique_titles.values(), key=str.casefold), blank_rows


def write_unified_titles(
    output_file: Path,
    rows: list[dict[str, str]],
) -> None:
    """Atomically rebuild the two-column standardized-title CSV."""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_name(f"{output_file.name}.tmp")

    try:
        with temporary_file.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary_file.replace(output_file)
    except Exception:
        if temporary_file.exists():
            temporary_file.unlink()
        raise


def write_audit_report(
    audit_file: Path,
    results: list[tuple[str, ClassificationResult]],
) -> None:
    """Write optional classification evidence without altering main output."""

    audit_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = audit_file.with_name(f"{audit_file.name}.tmp")
    rows = [
        {
            "Original Job Title": title,
            "Unified Position": result.root,
            "Detailed Subtype": result.subtype,
            "Confidence": f"{result.confidence:.2f}",
            "Decision Method": result.method,
            "Winning Signal": result.signal,
            "Alternative Roots": ", ".join(result.alternatives),
        }
        for title, result in results
    ]

    try:
        with temporary_file.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=AUDIT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary_file.replace(audit_file)
    except Exception:
        if temporary_file.exists():
            temporary_file.unlink()
        raise


def print_random_sample(
    rows: list[dict[str, str]],
    sample_size: int,
    seed: int | None,
) -> None:
    """Print a uniform sample without replacement for manual label review."""

    if sample_size == 0 or not rows:
        return

    actual_size = min(sample_size, len(rows))
    random_source = random.Random(seed) if seed is not None else random.SystemRandom()
    sampled_rows = random_source.sample(rows, actual_size)

    print()
    print(f"RANDOM LABEL VALIDATION SAMPLE ({actual_size} ROWS)")
    if seed is not None:
        print(f"Reproducible sample seed: {seed}")
    print("Copy the following lines for manual review:")
    print()

    for number, row in enumerate(sampled_rows, start=1):
        print(
            f"{number:02d}. {row['Original Job Title']} "
            f">>> {row['Unified Position']}"
        )


def unify_file(
    input_file: Path,
    output_file: Path,
    sample_size: int = 200,
    seed: int | None = None,
    audit_file: Path | None = None,
) -> Counter[str]:
    titles, blank_rows = read_unique_titles(input_file)
    classified = [(title, classify_title_explained(title)) for title in titles]
    rows = [
        {
            "Original Job Title": title,
            "Unified Position": result.root,
        }
        for title, result in classified
    ]
    write_unified_titles(output_file, rows)
    if audit_file is not None:
        write_audit_report(audit_file, classified)

    counts = Counter(row["Unified Position"] for row in rows)
    low_confidence = sum(result.confidence < 0.80 for _, result in classified)
    print(f"Read {len(titles)} unique nonblank title(s) from:")
    print(f"  {input_file}")
    if blank_rows:
        print(f"Skipped {blank_rows} blank row(s).")
    print(f"Wrote {len(rows)} standardized mapping row(s) to:")
    print(f"  {output_file}")
    print(f"Unified positions represented: {len(counts)}")
    print(f"Other/unclassified titles: {counts.get('Other', 0)}")
    print(f"Low-confidence mappings (<0.80): {low_confidence}")
    if audit_file is not None:
        print("Wrote classification evidence to:")
        print(f"  {audit_file}")
    print_random_sample(rows, sample_size, seed)
    return counts


def non_negative_integer(value: str) -> int:
    """Argparse converter for zero-or-greater integer options."""

    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map unique Czech/English job titles to canonical positions."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help=f"Unique-title CSV path (default: {DEFAULT_INPUT_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Unified-title CSV path (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--sample-size",
        type=non_negative_integer,
        default=200,
        help="Number of random output mappings printed for review (default: 200)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional integer seed for a reproducible validation sample",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
        help=(
            "Optional CSV path for subtype, confidence, winning signal, and "
            "alternative-root diagnostics"
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        validate_classifier()
        unify_file(
            arguments.input,
            arguments.output,
            sample_size=arguments.sample_size,
            seed=arguments.seed,
            audit_file=arguments.audit_output,
        )
    except PermissionError as exc:
        print(
            "ERROR: A CSV file is locked or inaccessible. Close it in Excel "
            f"and try again. Details: {exc}",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
