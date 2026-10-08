r"""Expand root-classified job titles into occupational specializations.

Default input:
    C:\Users\demps\Job_Scanner\Data_Mining\data\diagnostics\unique_job_titles\unified_job_titles.csv

Default output:
    C:\Users\demps\Job_Scanner\Data_Mining\data\diagnostics\job_classes\job_classes.csv

Input columns:
    Original Job Title, Unified Position

Output columns:
    Original Job Title, Root Position, Position Subtype, Secondary Subtype,
    Domain Tags, Technology Tags, Seniority

The classifier is deterministic, accent-insensitive, standard-library only,
and intentionally hierarchical:

* Root Position says what kind of occupation the title represents.
* Position Subtype says how that occupation specializes.
* Domain Tags describe the business/technical context.
* Technology Tags capture tools, languages, and platforms.
* Seniority captures career stage without changing the occupation.

This separation prevents contextual terms such as AI, banking, Java, or Azure
from replacing the actual occupational subtype.
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
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\03_taxonomy\root_classified_titles.csv"
)
DEFAULT_OUTPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\03_taxonomy\job_classes.csv"
)

INPUT_TITLE_COLUMN = "Original Job Title"
INPUT_ROOT_COLUMN = "Unified Position"
OUTPUT_FIELDS = [
    "Original Job Title",
    "Root Position",
    "Position Subtype",
    "Secondary Subtype",
    "Domain Tags",
    "Technology Tags",
    "Seniority",
]


@dataclass(frozen=True)
class SubtypeRule:
    """One root-dependent specialization rule."""

    label: str
    patterns: tuple[str, ...]
    priority: int = 100


@dataclass(frozen=True)
class TagRule:
    """One independent domain, technology, or seniority tag rule."""

    label: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class SubtypeCandidate:
    """A specialization detected at a concrete position in a title."""

    label: str
    priority: int
    start: int
    end: int


def subtype_rule(
    label: str,
    *patterns: str,
    priority: int = 100,
) -> SubtypeRule:
    return SubtypeRule(label, tuple(patterns), priority)


def tag_rule(label: str, *patterns: str) -> TagRule:
    return TagRule(label, tuple(patterns))


def normalize_text(value: str | None) -> str:
    """Return lowercase accent-free text with stable word boundaries."""

    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#./|]+", " ", text)
    return " ".join(text.split())


def normalize_header(value: str | None) -> str:
    if value is None:
        return ""
    return normalize_text(value.removeprefix("\ufeff").replace("_", " "))


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


# Secondary subtypes are intentionally conservative. A slash frequently
# separates technologies, locations, benefits, or business domains rather than
# two jobs. Both sides must therefore contain occupation evidence compatible
# with the accepted root before a second subtype is emitted.
ROOT_OCCUPATION_EVIDENCE: dict[str, tuple[str, ...]] = {
    "Manager": (
        r"\b(?:manager\w*|manazer\w*|lead(?:er)?|head|director\w*|vedouc\w*|owner|supervisor|chief|ceo|cfo|cto|cio|coo|cdo|ciso|cmo|vp)\b",
    ),
    "Architect": (r"\b(?:architect\w*|architekt\w*)\b",),
    "Analyst": (r"\b(?:analyst\w*|analytik\w*)\b",),
    "Scientist": (r"\b(?:scientist\w*|vedec\w*|vedkyn\w*)\b",),
    "Engineer": (r"\b(?:engineer\w*|inzenyr\w*)\b",),
    "Developer": (r"\b(?:developer\w*|vyvojar\w*|programator\w*|programmer\w*)\b",),
    "Administrator": (r"\b(?:administrator\w*|spravce\w*|admin)\b",),
    "Consultant": (r"\b(?:consultant\w*|konzultant\w*|poradce\w*)\b",),
    "Tester": (r"\b(?:tester\w*|test analyt\w*)\b",),
    "Specialist": (r"\b(?:specialist\w*|specialista\w*|expert\w*|odbornik\w*|metodik\w*|merchandis\w*)\b",),
    "Coordinator": (r"\b(?:coordinator\w*|koordinator\w*)\b",),
    "Technician": (r"\b(?:technician\w*|technik\w*|technolog\w*|metrolog\w*)\b",),
    "Assistant": (r"\b(?:assistant\w*|asistent\w*|back office|administrativni podpor\w*|fakturant\w*)\b",),
    "Teacher/Trainer": (r"\b(?:teacher\w*|trainer\w*|trener\w*|coach\w*|kouc\w*|ucitel\w*|lektor\w*)\b",),
    "Customer Service Representative": (
        r"\b(?:customer support|customer care|customer service|zakaznick\w* podpor\w*|zakaznick\w* pece|klientsk\w* servis\w*)\b",
    ),
}


FALLBACK_SUBTYPES: dict[str, str] = {
    "Manager": "Executive/General Manager",
    "Architect": "General Architect",
    "Analyst": "General Analyst",
    "Scientist": "General Scientist",
    "Researcher": "General Researcher",
    "Engineer": "General Engineer",
    "Developer": "General Software Developer",
    "Administrator": "General Administrator",
    "Consultant": "General Consultant",
    "Tester": "General Tester",
    "Designer": "General Designer",
    "Support Specialist": "General Support Specialist",
    "Customer Service Representative": "General Customer Service Representative",
    "Accountant": "General Accountant",
    "Controller": "General Controller",
    "Auditor": "General Auditor",
    "Recruiter": "General Recruiter",
    "Specialist": "General Specialist",
    "Sales Representative": "General Sales Representative",
    "Coordinator": "General Coordinator",
    "Technician": "General Technician",
    "Planner": "General Planner",
    "Assistant": "General Assistant",
    "Teacher/Trainer": "General Teacher/Trainer",
    "Healthcare Professional": "General Healthcare Professional",
    "Operator": "General Operator",
    "Driver": "General Driver",
    "Warehouse Worker": "General Warehouse Worker",
    "Production Worker": "General Production Worker",
    "Intern/Trainee": "General Internship/Traineeship",
    "Agent": "General Agent",
    "Officer": "General Officer",
    "Clerk": "General Clerk",
    "Other": "Unclassified",
}


ROOT_SUBTYPE_RULES: dict[str, tuple[SubtypeRule, ...]] = {
    "Manager": (
        subtype_rule(
            "Data/AI Manager",
            r"\b(?:head|lead|leader|manager\w*|director\w*|vedouc\w*|manazer\w*)\b(?:\s+\w+){0,4}\s+\b(?:data|analytics|business intelligence|bi|insights?)\b",
            r"\b(?:data|analytics|business intelligence|bi|insights?)\b(?:\s+\w+){0,4}\s+\b(?:head|lead|leader|manager\w*|director\w*|vedouc\w*|manazer\w*)\b",
            r"\b(?:head|lead|leader|manager\w*|director\w*)\b(?:\s+(?:of|for))?\s+\b(?:ai|machine learning|ml)\b",
            r"\b(?:ai|machine learning|ml)\b(?:\s+(?:team|function))?\s+\b(?:head|lead|leader|manager\w*|director\w*)\b",
            priority=156,
        ),
        subtype_rule(
            "R&D/Innovation Manager",
            r"\b(?:r and d|research|research area|innovation)\b.*\b(?:head|lead|leader|manager\w*|director\w*)\b",
            r"\b(?:head|lead|leader|manager\w*|director\w*)\b.*\b(?:r and d|research|innovation)\b",
            priority=155,
        ),
        subtype_rule(
            "IT/Engineering Manager",
            r"\b(?:backend|frontend|full stack|fullstack|software|engineering|automation|control systems?|devops|cloud|infrastructure|network|security|tech|technical)\b(?:\s+\w+){0,4}\s+\b(?:head|lead|leader|manager\w*|director\w*|vedouc\w*|manazer\w*)\b",
            r"\b(?:head|lead|leader|manager\w*|director\w*|vedouc\w*|manazer\w*)\b(?:\s+\w+){0,4}\s+\b(?:backend|frontend|full stack|fullstack|software|engineering|automation|control systems?|devops|cloud|infrastructure|network|security)\b",
            r"\b(?:c#|\.net|node(?:\.js)?|typescript|java|python)\b.*\b(?:head|lead|leader)\b",
            r"\b(?:head|lead|leader)\b.*\b(?:c#|\.net|node(?:\.js)?|typescript|java|python)\b",
            priority=154,
        ),
        subtype_rule(
            "Construction/BIM Manager",
            r"\b(?:bim|construction|staveb\w*|stavb\w*)\b",
            priority=153,
        ),
        subtype_rule(
            "Product Manager/Product Owner",
            r"\b(?:product|produkt\w*|value proposition|pricing|promo optimization)\b",
            r"\bcategory\b.*\b(?:manager\w*|management)\b",
            r"\bsegment\w*\s+(?:manager\w*|manazer\w*)\b",
            priority=152,
        ),
        subtype_rule(
            "Project Manager",
            r"\b(?:projectmanager|projektmanager)\w*\b",
            r"\b(?:project|program|implementation)\s+(?:manager|management|lead|leader|owner|director)\b",
            r"\b(?:projekt\w*|implementac\w*)\s+(?:manazer\w*|vedouc\w*|koordinator\w*)\b",
            r"\b(?:manager\w*|manazer\w*|vedouc\w*)\b(?:\s+\w+){0,4}\s+\b(?:project\w*|projekt\w*|realizac\w*)\b",
            r"\bpmo\b",
            priority=158,
        ),
        subtype_rule("Marketing/Brand Manager", r"\b(?:trade marketing|customer strategy|marketing strategy)\b", priority=149),
        subtype_rule("Sales/Account Manager", r"\b(?:sales|account|commercial|business development|merchant|trading|trade sales|export|tender|relationship|partnership|acquisition|akvizic\w*|obchod\w*|prodej\w*|fundraising)\b", priority=146),
        subtype_rule("Marketing/Brand Manager", r"\b(?:marketing|brand|media|content|campaign|growth|art|creative|e commerce|ecommerce|influencer|community|social ads|public affairs|pr and communications|crm)\b", priority=144),
        subtype_rule("Data/AI Manager", r"\b(?:data|analytics|ai|machine learning|ml|business intelligence|bi|insight)\b", priority=142),
        subtype_rule("IT/Engineering Manager", r"\b(?:it|technology|technical|engineering|software|development|devops|security|infrastructure|hardware|digitalization|digitalizac\w*|cmdb|network)\b", priority=140),
        subtype_rule("Finance/Risk Manager", r"\b(?:finance|financial|risk|credit|investment|portfolio|treasury|tax|accounting|controlling|audit|aml|compliance|m and a|transaction|forensic|cfo|cio|ekonom\w*)\b", priority=138),
        subtype_rule("HR/People Manager", r"\b(?:hr|people|human resources|talent|recruitment|personal\w*|payroll|reward|compensation|benefits)\b", priority=136),
        subtype_rule("Supply Chain/Procurement Manager", r"\b(?:supply chain|logistic\w*|transport\w*|procurement|purchasing|warehouse|sklad\w*|nakup\w*|sourcing|vendor|demand planning|forecasting|fleet|vozov\w*)\b", priority=134),
        subtype_rule("Customer Success/Experience Manager", r"\b(?:customer success|customer experience|customer service|customer lifecycle|zakaznick\w*|klientsk\w*|reklamac\w*)\b", priority=132),
        subtype_rule("Production/Quality Manager", r"\b(?:production|manufactur\w*|quality|qa|test|testing|plant|factory|vyrob\w*|jakost\w*|installation|maintenance|hse|safety|environment)\b", priority=141),
        subtype_rule("Operations/Service Delivery Manager", r"\b(?:operations?|operational|service delivery|delivery|facility|office|incident|change|process|proces\w*|realizac\w*|provozn\w*)\b", priority=120),
    ),
    "Specialist": (
        subtype_rule(
            "Customer Service Specialist",
            r"\b(?:komunikac\w* se zakaznik\w*|pece o klient\w*|pece o zakaznik\w*|customer engagement)\b",
            priority=150,
        ),
        subtype_rule(
            "Logistics/Supply Chain Specialist",
            r"\b(?:freight audit|transport audit|logistics audit)\b",
            priority=149,
        ),
        subtype_rule(
            "Sales/Commercial Specialist",
            r"\b(?:sales operations?|commercial operations?|crm and sales operations?)\b",
            priority=148,
        ),
        subtype_rule(
            "IT/Systems Specialist",
            r"\b(?:devops|cmdb|plm|projectwise|3d experience|successfactors)\b",
            r"\b(?:uzivatelsk\w* rozvoj\w* aplikac\w*|rozvoj\w* aplikac\w*)\b",
            priority=147,
        ),
        subtype_rule(
            "Risk/Business Continuity Specialist",
            r"\b(?:business continuity|crisis preparedness|emergency preparedness|krizov\w* pripravenost\w*|krizov\w* rizen\w*)\b",
            priority=146,
        ),
        subtype_rule(
            "Real Estate/Valuation Specialist",
            r"\b(?:real estate valuation|property valuation|odhadc\w*(?:\s*/\s*odhadkyn\w*)?\s+nemovit\w*|ocenovan\w* nemovit\w*)\b",
            priority=145,
        ),
        subtype_rule(
            "Data/Analytics Specialist",
            r"\b(?:financn\w* dat\w*|financial data)\b",
            priority=144,
        ),
        subtype_rule(
            "Finance/Banking Specialist",
            r"\b(?:uverov\w* strategi\w*|firemn\w* banker\w*|banker\w*)\b",
            priority=143,
        ),
        subtype_rule(
            "HR/People Specialist",
            r"\b(?:odmenovan\w*|remuneration|rewards?)\b",
            priority=142,
        ),
        subtype_rule("Marketing/Communications Specialist", r"\b(?:marketing|brand|social media|content|seo|ppc|campaign|communication|komunikac\w*|pr specialist|copywrit\w*|merchandis\w*)\b", priority=140),
        subtype_rule("Marketing/Communications Specialist", r"\bmarketink\w*\b", priority=141),
        subtype_rule("HR/People Specialist", r"\b(?:hr|human resources|people|payroll|compensation|benefits|employee|personaln\w*|mzd\w*|odmen\w*)\b", priority=138),
        subtype_rule("Cybersecurity Specialist", r"\b(?:cyber|security|secops|soc|bezpecnost\w*)\b", priority=136),
        subtype_rule("Data Governance/Quality Specialist", r"\b(?:data governance|data quality|master data|data steward|datov\w* kvalit\w*|business intelligence|data intelligence)\b", priority=134),
        subtype_rule("Data/Analytics Specialist", r"\b(?:data|analytics|datov\w*|dwh|reporting|sql|database|databaz\w*)\b", priority=133),
        subtype_rule("AI/Automation Specialist", r"\b(?:ai|artificial intelligence|generative ai|ai adoption|ai innovation|automation)\b", priority=132),
        subtype_rule("Compliance/AML Specialist", r"\b(?:compliance|aml|kyc|regulatory|risk|fraud|audit)\b", priority=130),
        subtype_rule("Legal Specialist", r"\b(?:legal|law|lawyer|counsel|pravnik\w*|advokat\w*|patent)\b", priority=128),
        subtype_rule("Insurance/Actuarial Specialist", r"\b(?:insurance|pojist\w*|actuar\w*)\b", priority=127),
        subtype_rule("Finance/Banking Specialist", r"\b(?:finance|financial|financ\w*|banking|banker\w*|bankovni|investment|investic\w*|treasury|tax|danov\w*|accounting|trader|broker|makler\w*|valuation|pricing|revenue|ifrs|controlling|collection|pohledav\w*|fakturac\w*|uver\w*)\b", priority=126),
        subtype_rule("Procurement Specialist", r"\b(?:procurement|purchas\w*|buyer|nakup\w*)\b", priority=124),
        subtype_rule("Logistics/Supply Chain Specialist", r"\b(?:logistic\w*|supply chain|transport\w*|freight|warehouse|sklad\w*|dispecer\w*)\b", priority=122),
        subtype_rule("Quality/Regulatory Specialist", r"\b(?:quality|kvalit\w*|jakost\w*|regulatory affairs|safety|hse|ehs)\b", priority=120),
        subtype_rule("Customer Service Specialist", r"\b(?:customer|client care|customer experience|zakaznick\w*|klient\w*|reklamac\w*|aftersales)\b", priority=119),
        subtype_rule("IT/Systems Specialist", r"\b(?:it|ict|information systems?|informacni system\w*|erp|sap|crm|infrastructure|infrastruktur\w*|cloud|devops|cmdb|plm|projectwise|application|aplikac\w*)\b", priority=118),
        subtype_rule("Product/Project Specialist", r"\b(?:product|produkt\w*|project|projekt\w*|pmo)\b", priority=116),
        subtype_rule("Sales/Commercial Specialist", r"\b(?:sales|commercial|business development|obchod\w*|prodej\w*)\b", priority=114),
        subtype_rule("Technical/Engineering Specialist", r"\b(?:technical|technick\w*|engineering|mechanical|electrical|automotive)\b", priority=112),
        subtype_rule("Manufacturing/Production Specialist", r"\b(?:manufactur\w*|production|vyrob\w*|packaging|continuous improvement|lean|opex)\b", priority=111),
        subtype_rule("Operations Specialist", r"\b(?:operations?|operational|service|facility|process|proces\w*|implementation|onboarding)\b", priority=110),
        subtype_rule("Administrative Specialist", r"\b(?:administrative|administrativ\w*|office|account creation|documentation)\b", priority=108),
    ),
    "Engineer": (
        subtype_rule("Data Engineer", r"\bdata engineer\w*\b", r"\bdatov\w* inzenyr\w*\b", r"\banalytics engineer\w*\b", r"\bdata platform engineer\w*\b", priority=150),
        subtype_rule("AI/ML Engineer", r"\b(?:ai|ml|machine learning|computer vision|llm)(?:\s+[a-z0-9+#.]+){0,2}\s+engineer\w*\b", priority=148),
        subtype_rule("DevOps/SRE Engineer", r"\b(?:devops|site reliability|sre)\b", priority=146),
        subtype_rule(
            "Automotive/Safety Engineer",
            r"\b(?:vehicle safety|automotive safety|functional safety|bezpecnost\w* voz\w*|airbag|pre crash|post crash|crash safety)\b",
            priority=145,
        ),
        subtype_rule("Security Engineer", r"\b(?:cyber|security|secops|soc|bezpecnost\w*)\b", priority=144),
        subtype_rule("QA/Test Engineer", r"\b(?:qa|quality assurance|test automation|test engineer|testing engineer|quality engineer)\b", priority=142),
        subtype_rule("Cloud/Platform Engineer", r"\b(?:cloud|platform|azure|aws|gcp|kubernetes)\b", priority=140),
        subtype_rule("Network/Infrastructure Engineer", r"\b(?:network|infrastructure|data center|datacenter|systems engineer|sitov\w*)\b", priority=138),
        subtype_rule("Software Engineer", r"\b(?:software|sw|backend|frontend|full stack|fullstack|application)\b.*\bengineer\w*\b", priority=136),
        subtype_rule("Automation/Control Engineer", r"\b(?:automation|control|state estimation|robotic\w*|mechatronic\w*|rizeni)\b", priority=134),
        subtype_rule("Mechanical/Automotive Engineer", r"\b(?:mechanical|vehicle|automotive|motorsport|dynamics|construction|stroj\w*)\b", priority=132),
        subtype_rule("Electrical/Electronics Engineer", r"\b(?:electrical|electronic\w*|hardware|hw|embedded|elektro\w*)\b", priority=130),
        subtype_rule("Process/Industrial Engineer", r"\b(?:process|proces\w*|industrial|industrialization|manufactur\w*|production|vyrob\w*)\b", priority=128),
    ),
    "Analyst": (
        subtype_rule("Data/BI Analyst", r"\b(?:data analyst|datov\w* analyt\w*|bi analyst|business intelligence analyst|analytics analyst|reporting analyst)\b", priority=150),
        subtype_rule(
            "Technical/R&D Analyst",
            r"\b(?:technical|technick\w*)\s+(?:analyst\w*|analytik\w*)\b.*\b(?:innovation|inovac\w*|patent|r and d|research)\b",
            r"\b(?:patent|innovation|inovac\w*|r and d|research)\b.*\b(?:analyst\w*|analytik\w*)\b",
            priority=151,
        ),
        subtype_rule("AI/ML Analyst", r"\b(?:ai|machine learning|ml)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=149),
        subtype_rule("IT/Systems Analyst", r"\b(?:it|ict|systems?|systemov\w*|technical)\b.*\b(?:analyst\w*|analytik\w*)\b", r"\b(?:analyst\w*|analytik\w*)\b.*\b(?:it|systems?|informacni system\w*)\b", priority=148),
        subtype_rule("Business/Process Analyst", r"\b(?:business|process|proces\w*|functional)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=146),
        subtype_rule("Security/Fraud Analyst", r"\b(?:security|cyber|soc|fraud|bezpecnost\w*)\b.*\b(?:analyst\w*|analytik\w*)\b", r"\b(?:analyst\w*|analytik\w*)\b.*\b(?:security|cyber|soc|fraud|bezpecnost\w*)\b", priority=144),
        subtype_rule("Risk/Credit Analyst", r"\b(?:risk|credit|compliance|aml|kyc|audit|rizik\w*)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=142),
        subtype_rule("Finance/Investment Analyst", r"\b(?:finance|financial|investment|pricing|valuation|treasury|accounting|fund|financ\w*|investic\w*|ocenovan\w*|m and a|middle office)\b.*\b(?:analyst\w*|analytik\w*)\b", r"\b(?:analyst\w*|analytik\w*)\b.*\b(?:finance|financial|investment|pricing|valuation|financ\w*|m and a|middle office)\b", priority=140),
        subtype_rule("QA/Test Analyst", r"\b(?:qa|test|quality)\b.*\b(?:analyst\w*|analytik\w*)\b", r"\b(?:analyst\w*|analytik\w*)\b.*\b(?:qa|test|quality)\b", priority=139),
        subtype_rule("Product Analyst", r"\bproduct\w* analyst\w*\b", r"\bprodukt\w* analyt\w*\b", priority=138),
        subtype_rule("Marketing/Customer Insights Analyst", r"\b(?:marketing|customer|consumer|insights?|digital campaign|webov\w*|seo)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=136),
        subtype_rule("HR/People Analyst", r"\b(?:hr|people|human resources|compensation|benefits|workforce)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=135),
        subtype_rule("Sales/Commercial Analyst", r"\b(?:sales|commercial|trade)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=135),
        subtype_rule("Operations/Supply Chain Analyst", r"\b(?:operations?|operational|supply chain|logistic\w*|procurement|purchasing)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=134),
        subtype_rule("Quantitative/Statistical Analyst", r"\b(?:quantitative|statistical|mathematic\w*|modeling|modelling)\b", priority=133),
        subtype_rule("Economic/Policy Analyst", r"\b(?:economic|economist|ekonom\w*|policy|politic\w*|fiscal|research)\b.*\b(?:analyst\w*|analytik\w*)\b", priority=132),
    ),
    "Developer": (
        subtype_rule("Full-Stack Developer", r"\b(?:full stack|fullstack)\b", priority=150),
        subtype_rule("Backend Developer", r"\b(?:backend|back end|server side)\b", priority=148),
        subtype_rule("Frontend Developer", r"\b(?:frontend|front end|web ui)\b", priority=146),
        subtype_rule("Mobile Developer", r"\b(?:mobile|android|ios|flutter)\b", priority=144),
        subtype_rule("Data/Database Developer", r"\b(?:data|database|databaz\w*|dwh|warehouse|sql|pl/sql|etl|bi developer)\b", priority=142),
        subtype_rule("Enterprise Applications Developer", r"\b(?:sap|abap|erp|crm|salesforce|servicenow|sharepoint)\b", priority=140),
        subtype_rule("Embedded/Systems Developer", r"\b(?:embedded|firmware|hardware|automotive|c\+\+|c#|systems? developer)\b", priority=138),
        subtype_rule("Automation/Low-Code Developer", r"\b(?:automation|low code|no code|power platform|rpa)\b", priority=136),
        subtype_rule("General Software Developer", r"\b(?:software|sw|web|application|aplikac\w*)\b", priority=110),
    ),
    "Consultant": (
        subtype_rule("SAP/ERP Consultant", r"\b(?:sap|erp|dynamics 365|oracle)\b", priority=150),
        subtype_rule("Security Consultant", r"\b(?:data security|information security|cybersecurity|security|cyber|bezpecnost\w*)\b", priority=149),
        subtype_rule("AI/Transformation Consultant", r"\b(?:ai|artificial intelligence|digital transformation|change management|transformac\w*)\b", priority=148),
        subtype_rule("Data/Analytics Consultant", r"\b(?:data|analytics|bi|business intelligence)\b", priority=145),
        subtype_rule("IT/Technology Consultant", r"\b(?:it|ict|technology|technical|software|system\w*)\b", priority=138),
        subtype_rule("Finance/Transactions Consultant", r"\b(?:finance|financial|valuation|m and a|m&a|due diligence|accounting|leasing|banking|ocenovan\w*|danov\w*|dph)\b", priority=136),
        subtype_rule("Business/Process Consultant", r"\b(?:business|process|proces\w*|operations?|implementation)\b", priority=134),
        subtype_rule("Marketing/Sales Consultant", r"\b(?:marketing|sales|commercial|presales|obchod\w*|prodej\w*|pricing)\b", priority=133),
        subtype_rule("Customer Experience Consultant", r"\b(?:customer experience|customer insights|client|klient\w*|travel)\b", priority=132),
        subtype_rule("Technical/Infrastructure Consultant", r"\b(?:cloud|infrastructure|network|vmware|virtual desktop|automotive|zabbix|nsx)\b", priority=131),
        subtype_rule("Real Estate Consultant", r"\b(?:real estate|property|nemovit\w*|pronaj\w*)\b", priority=130),
        subtype_rule("Management/Strategy Consultant", r"\b(?:management|strategy|strategic|organizational)\b", priority=132),
    ),
    "Administrator": (
        subtype_rule("HR/Finance Administrator", r"\bfinanc\w*\b.*\b(?:personal\w*|hr)\b", r"\b(?:personal\w*|hr)\b.*\bfinanc\w*\b", priority=151),
        subtype_rule("Database Administrator", r"\b(?:database|databaz\w*|dba)\b", priority=150),
        subtype_rule("Security/Identity Administrator", r"\b(?:security|identity|access|iam|bezpecnost\w*)\b", priority=145),
        subtype_rule("Cloud Administrator", r"\b(?:cloud|azure|aws|gcp)\b", priority=142),
        subtype_rule("Network/Infrastructure Administrator", r"\b(?:network|infrastructure|infrastruktur\w*|data center|datacenter|sit\w*)\b", priority=140),
        subtype_rule("Systems/Server Administrator", r"\b(?:systems?|systemov\w*|server|linux|windows|virtualization|virtualizac\w*)\b", priority=138),
        subtype_rule("Application Administrator", r"\b(?:application|aplikac\w*|erp|sap|crm|software|sw)\b", priority=136),
        subtype_rule("IT Administrator", r"\b(?:it|ict|information systems?|informacni system\w*)\b", priority=130),
        subtype_rule("HR/Payroll Administrator", r"\b(?:hr|payroll|mzd\w*|personal\w*)\b", priority=128),
        subtype_rule("Procurement Administrator", r"\b(?:procurement|purchasing|nakup\w*)\b", priority=126),
        subtype_rule("Finance Administrator", r"\b(?:finance|financial|financ\w*|reporting|investment|portfolio|budget|rozpoct\w*|kalkulac\w*|uver\w*)\b", priority=124),
        subtype_rule("Customer Service Administrator", r"\b(?:customer service|zakaznick\w* servis\w*|zakaznick\w*)\b", priority=122),
        subtype_rule("Production Administrator", r"\b(?:production|vyrob\w*|logistic\w*|warehouse|sklad\w*)\b", priority=121),
        subtype_rule("Business/Office Administrator", r"\b(?:office|administrativ\w*|administrac\w*|business|sales|obchod\w*|referent\w*|grant\w*)\b", priority=120),
    ),
    "Sales Representative": (
        subtype_rule("Business Development Representative", r"\b(?:business development|bdr)\b", priority=150),
        subtype_rule("Sales Development Representative", r"\b(?:sales development|sdr)\b", priority=148),
        subtype_rule("Account Executive", r"\baccount executive\b", priority=146),
        subtype_rule("Presales/Solutions Sales Representative", r"\b(?:presales|pre sales|solution sales|sales engineer)\b", priority=144),
        subtype_rule("Inside Sales/Telesales Representative", r"\b(?:inside sales|telesales|telephone|telefon\w*|call\w*)\b", priority=142),
        subtype_rule("Partnership Representative", r"\b(?:partnership|partner development|affiliate)\b", priority=140),
        subtype_rule("Retail Sales Representative", r"\b(?:retail|store|prodejna|pobock\w*)\b", priority=138),
        subtype_rule("Field Sales Representative", r"\b(?:field sales|territory sales|regional sales representative|obchodni zastup\w*|obchodnik\w*|prodejce\w*)\b", priority=130),
    ),
    "Architect": (
        subtype_rule("AI Architect", r"\b(?:ai|artificial intelligence|machine learning)\b", priority=150),
        subtype_rule("Data Architect", r"\b(?:data|datov\w*|dwh|database)\b", priority=148),
        subtype_rule("Security Architect", r"\b(?:security|cyber|bezpecnost\w*)\b", priority=146),
        subtype_rule("Cloud/Infrastructure Architect", r"\b(?:cloud|azure|aws|gcp|infrastructure|network)\b", priority=144),
        subtype_rule("Enterprise Architect", r"\benterprise\b", priority=142),
        subtype_rule("Solution Architect", r"\b(?:solution|reseni)\w*\b", priority=140),
        subtype_rule("Software/Application Architect", r"\b(?:software|application|aplikac\w*|web)\b", priority=138),
        subtype_rule("Systems/Integration Architect", r"\b(?:systems?|systemov\w*|integration|integrac\w*)\b", priority=136),
    ),
    "Support Specialist": (
        subtype_rule("IT Helpdesk/Service Desk", r"\b(?:helpdesk|help desk|service desk|1st level|first level|l1 support|2nd level|second level|l2 support)\b", priority=150),
        subtype_rule("HR/People Support", r"\b(?:hr support|people support|recruitment support|payroll support|personaln\w* podpor\w*)\b", priority=148),
        subtype_rule("Application/Software Support", r"\b(?:application|aplikac\w*|software|sw|sap|erp|crm)\b", priority=145),
        subtype_rule("Infrastructure/Network Support", r"\b(?:network|infrastructure|server|systems?|cloud|hardware|hw)\b", priority=142),
        subtype_rule("Data/Analytics Support", r"\b(?:data|analytics|trading)\b", priority=140),
        subtype_rule("Business/Sales Support", r"\b(?:business|sales|commercial|prodej\w*|obchod\w*)\b", priority=138),
        subtype_rule("Customer Support", r"\b(?:customer|client|zakaznik\w*|klient\w*)\b", priority=136),
        subtype_rule("Technical Support", r"\b(?:technical|technick\w*|it support|podpora)\b", priority=130),
    ),
    "Controller": (
        subtype_rule("Financial Controller", r"\b(?:financial|finance|financ\w*|fp and a|fp&a)\b", priority=150),
        subtype_rule("Business/Commercial Controller", r"\b(?:business|commercial|sales|retail)\b", priority=145),
        subtype_rule("Cost/Project Controller", r"\b(?:cost|project|projekt\w*|construction)\b", priority=142),
        subtype_rule("Operations Controller", r"\b(?:operations?|operational|production|plant|supply chain)\b", priority=140),
        subtype_rule("Group/Reporting Controller", r"\b(?:group|reporting|consolidation)\b", priority=138),
        subtype_rule("Internal Control Specialist", r"\b(?:internal control|controls specialist)\b", priority=136),
    ),
    "Customer Service Representative": (
        subtype_rule("Claims/Complaints Representative", r"\b(?:claims?|complaints?|reklamac\w*)\b", priority=150),
        subtype_rule("Contact Centre Representative", r"\b(?:call cent\w*|contact cent\w*|zakaznick\w* link\w*)\b", priority=146),
        subtype_rule("Customer Onboarding Representative", r"\b(?:onboarding|implementation)\b", priority=144),
        subtype_rule("Customer Care Representative", r"\b(?:customer care|zakaznick\w* pece|pece o zakaznik\w*)\b", priority=142),
        subtype_rule("Customer Support Representative", r"\b(?:customer support|zakaznick\w* podpor\w*)\b", priority=140),
        subtype_rule("Client Service Representative", r"\b(?:client service|klientsk\w* servis\w*|customer service|zakaznick\w* servis\w*)\b", priority=138),
    ),
    "Technician": (
        subtype_rule("IT Technician", r"\b(?:it|ict|computer|pc|network|hardware|server)\b", priority=150),
        subtype_rule("Installation Technician", r"\b(?:installation|instalac\w*|instalacni|montaz\w*)\b", priority=146),
        subtype_rule("Quality/Test Technician", r"\b(?:quality|qa|test|kvalit\w*|jakost\w*|metrolog\w*)\b", priority=144),
        subtype_rule("Service/Maintenance Technician", r"\b(?:service|servis\w*|maintenance|udrzb\w*)\b", priority=142),
        subtype_rule("Electrical/Electronics Technician", r"\b(?:electrical|electronic\w*|elektro\w*|electric\w*)\b", priority=140),
        subtype_rule("Mechanical Technician", r"\b(?:mechanical|mechanik\w*|stroj\w*|construction|konstruk\w*)\b", priority=138),
        subtype_rule("Manufacturing/Process Technician", r"\b(?:production|manufactur\w*|process|proces\w*|technolog\w*|vyrob\w*)\b", priority=136),
        subtype_rule("Engineering/Development Technician", r"\b(?:development|vyvoj\w*|engineering|prototype)\b", priority=134),
    ),
    "Accountant": (
        subtype_rule("Accounts Payable Accountant", r"\b(?:accounts payable|account payable|ap accountant|dodavatel\w*)\b", priority=150),
        subtype_rule("Accounts Receivable/Billing Accountant", r"\b(?:accounts receivable|account receivable|ar accountant|billing|fakturant\w*|pohledav\w*)\b", priority=148),
        subtype_rule("Payroll Accountant", r"\b(?:payroll|mzdov\w*|mzdy)\b", priority=146),
        subtype_rule("Tax Accountant", r"\b(?:tax|danov\w*)\b", priority=144),
        subtype_rule("Revenue/Intercompany Accountant", r"\b(?:revenue|intercompany)\b", priority=142),
        subtype_rule("Chief/General Ledger Accountant", r"\b(?:chief accountant|head accountant|hlavni ucetni|general ledger|gl accountant)\b", priority=140),
        subtype_rule("Financial Accountant", r"\b(?:financial|finance|financ\w*(?:\s*/\s*)?ucetn\w*)\b", priority=138),
    ),
    "Coordinator": (
        subtype_rule("Project/Program Coordinator", r"\b(?:project|program|projekt\w*|pmo)\b", priority=150),
        subtype_rule("IT/Incident Coordinator", r"\b(?:it|ict|incident|systems?|technology)\b", priority=146),
        subtype_rule("HR/People Coordinator", r"\b(?:hr|people|human resources|person\w*|recruitment)\b", priority=144),
        subtype_rule("Logistics/Supply Chain Coordinator", r"\b(?:logistic\w*|supply chain|transport\w*|warehouse|sklad\w*|purchasing|procurement)\b", priority=142),
        subtype_rule("Sales/Marketing Coordinator", r"\b(?:sales|marketing\w*|commercial|brand|event|obchod\w*)\b", priority=140),
        subtype_rule("Operations/Service Coordinator", r"\b(?:operations?|operational|service|production|facility|provozn\w*|fieldwork|allocator)\b", priority=139),
        subtype_rule("Finance/Administrative Coordinator", r"\b(?:finance|financial|administrative|office|document\w*)\b", priority=138),
    ),
    "Assistant": (
        subtype_rule("Executive/Personal Assistant", r"\b(?:executive|personal|management|director|board|ceo|partner)\b", priority=150),
        subtype_rule("Finance/Accounting Assistant", r"\b(?:finance|financial|accounting|ucetn\w*|tax|danov\w*|fakturant\w*)\b", priority=146),
        subtype_rule("Sales/Business Assistant", r"\b(?:sales|business|commercial|obchod\w*|prodej\w*)\b", priority=144),
        subtype_rule("Project/Team Assistant", r"\b(?:project|team|projekt\w*)\b", priority=142),
        subtype_rule("Office/Reception Assistant", r"(?<!back )\boffice\b", r"\b(?:reception|recepc\w*|front office)\b", priority=140),
        subtype_rule("Administrative/Back-Office Assistant", r"\b(?:administrative|administrativ\w*|back office|documentation|dokument\w*)\b", priority=138),
    ),
    "Tester": (
        subtype_rule("Automation Tester", r"\b(?:automation|automatiz\w*|selenium|playwright)\b", priority=150),
        subtype_rule("Integration/API Tester", r"\b(?:integration|integrac\w*|api|soap|rest)\b", priority=146),
        subtype_rule("Security/Penetration Tester", r"\b(?:security|cyber|penetration|pentest)\b", priority=144),
        subtype_rule("Automotive/Embedded Tester", r"\b(?:automotive|vehicle|embedded|hil|hardware)\b", priority=142),
        subtype_rule("Mobile/Web Tester", r"\b(?:mobile|web|android|ios|browser)\b", priority=140),
        subtype_rule("Functional/QA Tester", r"\b(?:functional|qa|quality assurance|test analyst|test analyt\w*)\b", priority=138),
        subtype_rule("Manual Software Tester", r"\b(?:manual|manualni|rucni)\b", priority=137),
        subtype_rule("General Software Tester", r"\b(?:software|sw)\b", priority=136),
    ),
    "Designer": (
        subtype_rule("UX/UI/Product Designer", r"\b(?:ux|ui|product design|product designer|product discovery|business discovery|interaction)\b", priority=150),
        subtype_rule("Fashion/Product Designer", r"\b(?:fashion|garment|apparel|clothing|textile|footwear)\b", priority=149),
        subtype_rule("Graphic/Visual Designer", r"\b(?:graphic|grafik\w*|visual|web design)\b", priority=146),
        subtype_rule("Brand/Communication Designer", r"\b(?:brand|communication|komunikac\w*|crm)\b", priority=144),
        subtype_rule("Mechanical/Technical Designer", r"\b(?:mechanical|technical|hardware|hw|cad|construction|konstruk\w*)\b", priority=142),
        subtype_rule("Service/CX Designer", r"\b(?:service design|customer experience|cx)\b", priority=140),
    ),
    "Planner": (
        subtype_rule("Demand/Supply Planner", r"\b(?:demand|supply|s and op|s&op)\b", priority=150),
        subtype_rule("Production Planner", r"\b(?:production|manufactur\w*|vyrob\w*)\b", priority=146),
        subtype_rule("Logistics/Transport Planner", r"\b(?:logistic\w*|transport\w*|shipping|route|gas scheduler)\b", priority=144),
        subtype_rule("Financial Planner", r"\b(?:finance|financial|budget|rozpoct\w*)\b", priority=142),
        subtype_rule("Media Planner", r"\b(?:media|digital media|advertising)\b", priority=140),
        subtype_rule("Project/Capacity Planner", r"\b(?:project|capacity|construction|stavb\w*)\b", priority=138),
    ),
    "Recruiter": (
        subtype_rule("IT/Technical Recruiter", r"\b(?:it|tech|technical|engineering|software)\b", priority=150),
        subtype_rule("Executive Search Recruiter", r"\b(?:executive search|headhunt\w*)\b", priority=146),
        subtype_rule("Talent Acquisition Specialist", r"\b(?:talent acquisition|talent sourcing)\b", priority=142),
    ),
    "Scientist": (
        subtype_rule("Data Scientist", r"\bdata scientist\w*\b", priority=150),
        subtype_rule("AI/ML Scientist", r"\b(?:machine learning|ml|ai|artificial intelligence)\b", priority=146),
        subtype_rule("Statistical/Quantitative Scientist", r"\b(?:statistical|statistics|quantitative|mathematic\w*|modeler|modeller)\b", priority=142),
        subtype_rule("Domain Scientist", r"\b(?:biology|chemistry|chemical|physics|medical|clinical|environmental)\b", priority=138),
    ),
    "Auditor": (
        subtype_rule("IT Auditor", r"\b(?:it|ict|technology|software|cyber|security|licence)\b", priority=150),
        subtype_rule("Financial Auditor", r"\b(?:finance|financial|accounting|tax|banking)\b", priority=146),
        subtype_rule("Internal Auditor", r"\b(?:internal|interni)\b", priority=142),
        subtype_rule("Compliance/Quality Auditor", r"\b(?:compliance|quality|regulatory|process)\b", priority=140),
    ),
    "Officer": (
        subtype_rule("Compliance/AML Officer", r"\b(?:compliance|aml|kyc|regulatory)\b", priority=150),
        subtype_rule("Risk/Finance Officer", r"\b(?:risk|finance|financial|credit|investment)\b", priority=146),
        subtype_rule("Legal/Privacy Officer", r"\b(?:legal|privacy|data protection|dpo)\b", priority=142),
        subtype_rule("Operations Officer", r"\boperations?\b", priority=140),
    ),
    "Intern/Trainee": (
        subtype_rule("Compliance/Risk Internship", r"\b(?:compliance|aml|kyc|risk|regulatory)\b", priority=151),
        subtype_rule("Data/Analytics Internship", r"\b(?:data|analytics|bi|machine learning|ai)\b", priority=150),
        subtype_rule("Engineering/IT Internship", r"\b(?:engineering|engineer|it|software|technology|technical)\b", priority=146),
        subtype_rule("Finance Internship", r"\b(?:finance|financial|accounting|banking|investment)\b", priority=144),
        subtype_rule("HR Internship", r"\b(?:hr|human resources|people|recruitment)\b", priority=142),
        subtype_rule("Marketing/Sales Internship", r"\b(?:marketing|sales|business development|commercial)\b", priority=140),
    ),
    "Teacher/Trainer": (
        subtype_rule("Academic Teacher", r"\b(?:school|university|biology|mathematics|teacher|ucitel\w*)\b", priority=150),
        subtype_rule("Sales Trainer", r"\b(?:sales|telesales|obchod\w*)\b", priority=146),
        subtype_rule("Technical Trainer", r"\b(?:technical|technology|it|software)\b", priority=144),
        subtype_rule("Corporate/Professional Trainer", r"\b(?:corporate|business|professional|learning|coach|kouc\w*)\b", priority=140),
    ),
    "Clerk": (
        subtype_rule("Master Data Clerk", r"\b(?:master data|data)\b", priority=150),
        subtype_rule("Finance/Billing Clerk", r"\b(?:finance|financial|billing|invoice|accounting)\b", priority=146),
        subtype_rule("Administrative Clerk", r"\b(?:administrative|office|document\w*)\b", priority=140),
    ),
    "Operator": (
        subtype_rule("IT/Data-Centre Operator", r"\b(?:it|data center|datacenter|systems?|network|dwh|database)\b", priority=150),
        subtype_rule("Logistics/Dispatch Operator", r"\b(?:dispatch|dispecer\w*|logistic\w*|transport\w*)\b", priority=146),
        subtype_rule("Production Operator", r"\b(?:production|manufactur\w*|vyrob\w*|machine)\b", priority=142),
    ),
    "Researcher": (
        subtype_rule("Market/User Researcher", r"\b(?:market|user|ux|customer|consumer)\b", priority=150),
        subtype_rule("Academic/Scientific Researcher", r"\b(?:academic|scientific|university|postdoc|science|vyzkum\w*)\b", priority=146),
        subtype_rule("Applied Researcher", r"\b(?:applied|industrial|r and d|r&d)\b", priority=142),
    ),
    "Agent": (
        subtype_rule("Customer Service Agent", r"\b(?:customer|client|care|service|zakaznik\w*)\b", priority=150),
        subtype_rule("Sales/Marketing Agent", r"\b(?:sales|marketing|ppc|commercial)\b", priority=146),
        subtype_rule("Operations Agent", r"\b(?:operations?|operative|logistics)\b", priority=142),
    ),
    "Healthcare Professional": (
        subtype_rule("Pharmacy/Optometry Professional", r"\b(?:pharmac\w*|farmaceut\w*|optometr\w*)\b", priority=150),
        subtype_rule("Medical/Clinical Professional", r"\b(?:doctor|physician|nurse|medical|clinical|lekar\w*|sestra)\b", priority=146),
    ),
    "Driver": (
        subtype_rule("Delivery/Courier Driver", r"\b(?:delivery|courier|kuryr\w*)\b", priority=150),
        subtype_rule("Commercial Driver", r"\b(?:truck|lorry|bus|kamion\w*|naklad\w*)\b", priority=146),
    ),
    "Warehouse Worker": (
        subtype_rule("Warehouse Picker/Packer", r"\b(?:picker|packer|pick|pack)\b", priority=150),
        subtype_rule("Warehouse Handler", r"\b(?:handler|forklift|manipul\w*)\b", priority=146),
    ),
    "Production Worker": (
        subtype_rule("Assembly Worker", r"\b(?:assembly|montaz\w*)\b", priority=150),
        subtype_rule("Machine/Production Worker", r"\b(?:machine|production|vyrob\w*)\b", priority=146),
    ),
}


DOMAIN_RULES: tuple[TagRule, ...] = (
    tag_rule("Data & Analytics", r"\b(?:data|analytics|analytik\w*|business intelligence|bi|dwh|reporting|datawarehouse)\b"),
    tag_rule("AI & Machine Learning", r"\b(?:ai|artificial intelligence|machine learning|ml|llm|generative ai|computer vision|deep learning)\b"),
    tag_rule("Cybersecurity", r"\b(?:cyber\w*|cybersecurity|security|secops|soc|iam|information security|kyber\w*|informac\w* bezpecnost\w*|bezpecnost\w* (?:it|ot|dat|informac\w*))\b"),
    tag_rule("Cloud & Infrastructure", r"\b(?:cloud|infrastructure|network|server|data center|datacenter|platform|devops|sre)\b"),
    tag_rule("IT & Software", r"\b(?:it|ict|software|developer|vyvojar\w*|programator\w*|application|aplikac\w*|systems?)\b"),
    tag_rule("Finance & Banking", r"\b(?:finance|financial|banking|banker|bankovni|accounting|controller|treasury|investment|investic\w*|valuation|pricing|tax|danov\w*)\b"),
    tag_rule("Insurance", r"\b(?:insurance|pojist\w*|actuar\w*)\b"),
    tag_rule("Compliance & Risk", r"\b(?:compliance|risk|credit|aml|kyc|regulatory|audit|fraud)\b"),
    tag_rule("Business Continuity & Resilience", r"\b(?:business continuity|crisis preparedness|emergency preparedness|krizov\w* pripravenost\w*|krizov\w* rizen\w*)\b"),
    tag_rule("Sales & Business Development", r"\b(?:sales|business development|commercial|account manager|obchod\w*|prodej\w*|akvizic\w*)\b"),
    tag_rule("Marketing & Communications", r"\b(?:marketing|brand|communication|komunikac\w*|media|content|seo|ppc|campaign|pr specialist)\b"),
    tag_rule("HR & Recruitment", r"\b(?:hr|human resources|people|talent|recruit\w*|personaln\w*|payroll|mzd\w*)\b"),
    tag_rule("Customer Service", r"\b(?:customer|client|customer care|customer service|zakaznik\w*|zakaznick\w*|klient\w*|reklamac\w*)\b"),
    tag_rule("Product Management", r"\b(?:product manager|product owner|produkt\w* manazer|produkt\w* owner)\b"),
    tag_rule("Project & Program Management", r"\b(?:project|program|projekt\w*|pmo)\b"),
    tag_rule("Supply Chain & Logistics", r"\b(?:supply chain|logistic\w*|transport\w*|warehouse|sklad\w*|freight|dispatch)\b"),
    tag_rule("Procurement", r"\b(?:procurement|purchas\w*|buyer|nakup\w*)\b"),
    tag_rule("Manufacturing & Production", r"\b(?:manufactur\w*|production|factory|plant|industrial|vyrob\w*)\b"),
    tag_rule("Engineering & R&D", r"\b(?:engineering|engineer\w*|inzenyr\w*|r and d|research and development|innovation|inovac\w*)\b"),
    tag_rule("Quality & Safety", r"\b(?:quality|qa|safety|functional safety|vehicle safety|kvalit\w*|jakost\w*|bezpecnost\w* voz\w*)\b"),
    tag_rule("Automotive & Mobility", r"\b(?:automotive|vehicle|car|mobility|rail|railway|tram|locomotive|vozidl\w*|kolej\w*)\b"),
    tag_rule("Energy & Utilities", r"\b(?:energy|energet\w*|electricity|power|gas|utility|utilities|oze)\b"),
    tag_rule("Healthcare & Pharma", r"\b(?:healthcare|healthtech|medical|clinical|pharma|pharmacy|farmaceut\w*|hospital)\b"),
    tag_rule("Legal", r"\b(?:legal|law|lawyer|counsel|pravnik\w*|advokat\w*|patent)\b"),
    tag_rule("Real Estate & Construction", r"\b(?:real estate|property|construction|building|staveb\w*|nemovit\w*)\b"),
    tag_rule("Retail & E-commerce", r"\b(?:retail|e commerce|ecommerce|store|shop|prodejn\w*)\b"),
    tag_rule("Education & Research", r"\b(?:education|school|university|teacher|training|research|science|vyzkum\w*)\b"),
    tag_rule("Telecommunications", r"\b(?:telecom|telecommunication|telco|mobile operator)\b"),
    tag_rule("Media & Entertainment", r"\b(?:media|gaming|game|entertainment|film|video|music|publishing)\b"),
)


TECHNOLOGY_RULES: tuple[TagRule, ...] = (
    tag_rule("Python", r"\bpython\b"),
    tag_rule("SQL", r"\bsql\b", r"\bpl/sql\b"),
    tag_rule("PostgreSQL", r"\bpostgres(?:ql)?\b"),
    tag_rule("Oracle", r"\boracle\b"),
    tag_rule("MS SQL Server", r"\b(?:mssql|ms sql|sql server)\b"),
    tag_rule("MySQL", r"\bmysql\b"),
    tag_rule("Java", r"\bjava\b(?!script)"),
    tag_rule("Kotlin", r"\bkotlin\b"),
    tag_rule("Scala", r"\bscala\b"),
    tag_rule("JavaScript", r"\bjavascript\b", r"\bjs\b"),
    tag_rule("TypeScript", r"\btypescript\b"),
    tag_rule("Node.js", r"\bnode(?:\.js|js)?\b"),
    tag_rule("React", r"\breact(?:\.js|js)?\b"),
    tag_rule("Angular", r"\bangular\b"),
    tag_rule("PHP", r"\bphp\b"),
    tag_rule(".NET", r"(?:^|\s)\.?(?:net|dotnet)(?:\s|$)"),
    tag_rule("C#", r"\bc#(?=\s|$)"),
    tag_rule("C/C++", r"\bc\+\+(?=\s|$)", r"\bc/c\+\+(?=\s|$)"),
    tag_rule("Rust", r"\brust\b"),
    tag_rule("Ruby/Rails", r"\bruby\b", r"\brails\b"),
    tag_rule("ABAP", r"\babap\b"),
    tag_rule("SAP", r"\bsap\b"),
    tag_rule("Salesforce", r"\bsalesforce\b"),
    tag_rule("Power BI", r"\bpower bi\b"),
    tag_rule("Tableau", r"\btableau\b"),
    tag_rule("Looker", r"\blooker\b"),
    tag_rule("Excel", r"\bexcel\b"),
    tag_rule("Azure", r"\bazure\b"),
    tag_rule("AWS", r"\baws\b"),
    tag_rule("GCP", r"\bgcp\b", r"\bgoogle cloud\b"),
    tag_rule("Docker", r"\bdocker\b"),
    tag_rule("Kubernetes", r"\bkubernetes\b", r"\bk8s\b"),
    tag_rule("Terraform", r"\bterraform\b"),
    tag_rule("Linux", r"\blinux\b"),
    tag_rule("Snowflake", r"\bsnowflake\b"),
    tag_rule("Databricks", r"\bdatabricks\b"),
    tag_rule("Spark", r"\b(?:apache )?spark\b"),
    tag_rule("dbt", r"\bdbt\b"),
    tag_rule("Kafka", r"\bkafka\b"),
    tag_rule("Selenium", r"\bselenium\b"),
    tag_rule("Playwright", r"\bplaywright\b"),
    tag_rule("ServiceNow", r"\bservicenow\b"),
    tag_rule("BIM", r"\bbim\b"),
    tag_rule("PLM", r"\bplm\b"),
    tag_rule("3DEXPERIENCE", r"\b(?:3d experience|3dexperience)\b"),
    tag_rule("ProjectWise", r"\bprojectwise\b"),
    tag_rule("CMDB", r"\bcmdb\b"),
)


SENIORITY_RULES: tuple[TagRule, ...] = (
    tag_rule("Intern/Trainee", r"\bintern(?:ship)?\b", r"\btrainee(?:ship)?\b", r"\bstazist\w*\b"),
    tag_rule("Junior/Entry-level", r"\bjunior\w*\b", r"\bentry level\b", r"\bgraduate\w*\b", r"\babsolvent\w*\b"),
    tag_rule("Medior/Mid-level", r"\bmedior\w*\b", r"\bmid level\b", r"\bmiddle\b"),
    tag_rule("Senior", r"\bsenior\w*\b", r"\bsr\b"),
    tag_rule("Lead/Head", r"\b(?:lead|leader|teamlead|teamleader|head|vedouc\w*|lidr\w*)\b"),
    tag_rule("Executive", r"\b(?:chief|director\w*|redit\w*|ceo|cfo|cto|cio|coo|cdo|ciso|cmo|vp)\b"),
)


REGRESSION_CASES: tuple[tuple[str, str, str], ...] = (
    ("Senior Product Owner", "Manager", "Product Manager/Product Owner"),
    ("Projektový manažer implementace", "Manager", "Project Manager"),
    ("Senior Key Account Manager", "Manager", "Sales/Account Manager"),
    ("Marketing & Brand Manager", "Manager", "Marketing/Brand Manager"),
    ("Head of Data Engineering & AI", "Manager", "Data/AI Manager"),
    ("Service Delivery Manager", "Manager", "Operations/Service Delivery Manager"),
    ("Service Delivery Manager / SW Development Lead, Top Projekty", "Manager", "IT/Engineering Manager"),
    ("Test Lead / Senior QA Tester", "Manager", "Production/Quality Manager"),
    ("Specialista marketingové komunikace", "Specialist", "Marketing/Communications Specialist"),
    ("Master Data Specialist", "Specialist", "Data Governance/Quality Specialist"),
    ("AML Specialista", "Specialist", "Compliance/AML Specialist"),
    ("IT Specialista bezpečnosti informací", "Specialist", "Cybersecurity Specialist"),
    ("Data Engineer", "Engineer", "Data Engineer"),
    ("Senior Machine Learning Engineer", "Engineer", "AI/ML Engineer"),
    ("Principal DevOps Engineer", "Engineer", "DevOps/SRE Engineer"),
    ("System Test Engineer", "Engineer", "QA/Test Engineer"),
    ("L1 Network Support Engineer", "Engineer", "Network/Infrastructure Engineer"),
    ("Business Analyst", "Analyst", "Business/Process Analyst"),
    ("IT Business Analyst", "Analyst", "IT/Systems Analyst"),
    ("Financial Data Analyst", "Analyst", "Data/BI Analyst"),
    ("Security Analyst", "Analyst", "Security/Fraud Analyst"),
    ("Senior Kotlin Developer", "Developer", "General Software Developer"),
    ("Oracle PL/SQL Developer", "Developer", "Data/Database Developer"),
    ("Junior PHP Full-Stack Developer", "Developer", "Full-Stack Developer"),
    ("Senior Backend Developer (PHP)", "Developer", "Backend Developer"),
    ("SAP konzultant", "Consultant", "SAP/ERP Consultant"),
    ("Analytics Implementation Consultant", "Consultant", "Data/Analytics Consultant"),
    ("SENIOR WINDOWS ADMINISTRÁTOR", "Administrator", "Systems/Server Administrator"),
    ("Data Center Administrator", "Administrator", "Network/Infrastructure Administrator"),
    ("Business Development Representative", "Sales Representative", "Business Development Representative"),
    ("Enterprise Architect", "Architect", "Enterprise Architect"),
    ("AI Architect", "Architect", "AI Architect"),
    ("IT Hardware & Support Specialist", "Support Specialist", "Infrastructure/Network Support"),
    ("Financial Controller", "Controller", "Financial Controller"),
    ("Customer Care Specialist", "Customer Service Representative", "Customer Care Representative"),
    ("QA TECHNIK - AUTOMOTIVE", "Technician", "Quality/Test Technician"),
    ("Payroll Accountant", "Accountant", "Payroll Accountant"),
    ("Sales Operations Coordinator", "Coordinator", "Sales/Marketing Coordinator"),
    ("OFFICE ASSISTANT", "Assistant", "Office/Reception Assistant"),
    ("Automation Tester", "Tester", "Automation Tester"),
    ("Service Product Designer", "Designer", "UX/UI/Product Designer"),
    ("Supply Chain Import Planner", "Planner", "Demand/Supply Planner"),
    ("Administrativní podpora / Back Office", "Assistant", "Administrative/Back-Office Assistant"),
    # Phrase-precedence corrections from the 200-row specialization audit.
    ("Data Security Consultant", "Consultant", "Security Consultant"),
    ("Software tester senior - FinTech", "Tester", "General Software Tester"),
    ("Trade Marketing & Customer Strategy Lead CZSK", "Manager", "Marketing/Brand Manager"),
    ("Freight Audit Senior Specialist", "Specialist", "Logistics/Supply Chain Specialist"),
    ("Manažer realizace stavebních projektů", "Manager", "Project Manager"),
    ("Projektmanager standardizace logistických procesů", "Manager", "Project Manager"),
    ("Senior/Lead Backend Engineer – AI / HR Tech", "Manager", "IT/Engineering Manager"),
    ("Business development manažer – senior pro segment komerční development", "Manager", "Sales/Account Manager"),
    ("Specialista na komunikaci se zákazníky", "Specialist", "Customer Service Specialist"),
    ("DevOps specialista pro datová uložiště", "Specialist", "IT/Systems Specialist"),
    ("Sales Representative", "Sales Representative", "General Sales Representative"),
    ("CRM a Sales Operations Specialist", "Specialist", "Sales/Commercial Specialist"),
    ("R&D inženýr-bezpečnost vozu", "Engineer", "Automotive/Safety Engineer"),
    ("Konzultant AI/Data transformace a change management", "Consultant", "AI/Transformation Consultant"),
    # Additional safe specialization coverage.
    ("Senior M&A Analyst/Advisor", "Analyst", "Finance/Investment Analyst"),
    ("Group Automation & Control Systems Lead", "Manager", "IT/Engineering Manager"),
    ("Patent Specialist / Technický analytik inovací", "Analyst", "Technical/R&D Analyst"),
    ("IT SPECIALISTA/ SPRÁVCE IT INFRASTRUKTURY", "Administrator", "Network/Infrastructure Administrator"),
    ("Specialista implementace úvěrových strategií", "Specialist", "Finance/Banking Specialist"),
    ("Metodik - specialista uživatelského rozvoje aplikací", "Specialist", "IT/Systems Specialist"),
    ("Odhadce/odhadkyně nemovitého majetku", "Specialist", "Real Estate/Valuation Specialist"),
    ("Specialista ProjectWise", "Specialist", "IT/Systems Specialist"),
    ("Testing Engineer", "Engineer", "QA/Test Engineer"),
    ("HR Support - BRIGÁDA", "Support Specialist", "HR/People Support"),
    ("Research Area (R&D) Lead", "Manager", "R&D/Innovation Manager"),
    ("DPH daňový konzultant/poradce", "Consultant", "Finance/Transactions Consultant"),
    ("SPECIALISTA FINANČNÍCH DAT", "Specialist", "Data/Analytics Specialist"),
    ("Provozní koordinátor stravování", "Coordinator", "Operations/Service Coordinator"),
    ("3D Experience PLM Solution Specialist", "Specialist", "IT/Systems Specialist"),
    ("Specialista CMDB", "Specialist", "IT/Systems Specialist"),
    ("BIM manažer", "Manager", "Construction/BIM Manager"),
    ("Analytik personální bezpečnosti", "Analyst", "Security/Fraud Analyst"),
    ("Finanční referent", "Administrator", "Finance Administrator"),
    ("Referent oddělení finančního reportingu", "Administrator", "Finance Administrator"),
    ("Firemní bankéř", "Specialist", "Finance/Banking Specialist"),
    ("Specialista krizové připravenosti", "Specialist", "Risk/Business Continuity Specialist"),
    ("Team Lead for Actors (Node.js/TypeScript)", "Manager", "IT/Engineering Manager"),
    ("SENIOR BUSINESS DISCOVERY DESIGNER", "Designer", "UX/UI/Product Designer"),
    ("Senior specialista odměňování", "Specialist", "HR/People Specialist"),
    ("ONLINE MARKETINKOVÝ SPECIALISTA", "Specialist", "Marketing/Communications Specialist"),
    ("C#/.NET Tech Lead", "Manager", "IT/Engineering Manager"),
    ("Senior Finanční/Účetní Specialista", "Accountant", "Financial Accountant"),
    ("Fieldwork Coordination Allocator", "Coordinator", "Operations/Service Coordinator"),
    ("Backend Tech Lead", "Manager", "IT/Engineering Manager"),
    ("Middle Office Analyst", "Analyst", "Finance/Investment Analyst"),
    ("SENIOR INTERNÍ AUDITOR", "Auditor", "Internal Auditor"),
    ("Vedoucí ekonomického oddělení", "Manager", "Finance/Risk Manager"),
    ("Koordinátor marketingu centra", "Coordinator", "Sales/Marketing Coordinator"),
    # Compatibility with the corrected upstream root classifier.
    ("Compliance Trainee", "Intern/Trainee", "Compliance/Risk Internship"),
    ("Asistent plánování výroby - Junior Supply Chain Planner", "Planner", "Demand/Supply Planner"),
    ("Fashion Developer pro vlastní kolekce", "Designer", "Fashion/Product Designer"),
    ("Finanční a personální administrativa", "Administrator", "HR/Finance Administrator"),
)


SECONDARY_REGRESSION_CASES: tuple[tuple[str, str, str], ...] = (
    ("Administrativní podpora / Back Office", "Assistant", ""),
    ("Senior/Lead Backend Engineer – AI / HR Tech", "Manager", ""),
    (
        "Service Delivery Manager / SW Development Lead, Top Projekty",
        "Manager",
        "Operations/Service Delivery Manager",
    ),
)


def find_column(fieldnames: Iterable[str | None], expected: str) -> str | None:
    wanted = normalize_header(expected)
    return next(
        (field for field in fieldnames if normalize_header(field) == wanted),
        None,
    )


def read_root_classified_titles(input_file: Path) -> list[tuple[str, str]]:
    """Read the two-column root-classified source without changing row order."""

    if not input_file.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_file}")
    if not input_file.is_file():
        raise ValueError(f"Input path is not a file: {input_file}")

    rows: list[tuple[str, str]] = []
    with input_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"Input CSV has no header: {input_file}")

        title_column = find_column(reader.fieldnames, INPUT_TITLE_COLUMN)
        root_column = find_column(reader.fieldnames, INPUT_ROOT_COLUMN)
        missing = [
            name
            for name, column in (
                (INPUT_TITLE_COLUMN, title_column),
                (INPUT_ROOT_COLUMN, root_column),
            )
            if column is None
        ]
        if missing:
            raise ValueError(
                "Input CSV is missing required column(s): " + ", ".join(missing)
            )

        assert title_column is not None
        assert root_column is not None
        for line_number, row in enumerate(reader, start=2):
            title = " ".join((row.get(title_column) or "").split())
            root = " ".join((row.get(root_column) or "").split())
            if not title:
                continue
            if root not in ROOT_TAXONOMY:
                raise ValueError(
                    f"Unknown root position {root!r} on CSV line {line_number}."
                )
            rows.append((title, root))

    return rows


def collect_subtype_candidates(
    normalized_title: str,
    root: str,
) -> list[SubtypeCandidate]:
    candidates: list[SubtypeCandidate] = []

    for rule in ROOT_SUBTYPE_RULES.get(root, ()):
        matches = [
            match
            for pattern in rule.patterns
            for match in re.finditer(pattern, normalized_title)
        ]
        if not matches:
            continue
        match = min(matches, key=lambda item: (item.start(), -(item.end() - item.start())))
        candidates.append(
            SubtypeCandidate(
                label=rule.label,
                priority=rule.priority,
                start=match.start(),
                end=match.end(),
            )
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.priority,
            candidate.start,
            -(candidate.end - candidate.start),
            candidate.label,
        ),
    )


def separator_between(
    normalized_title: str,
    root: str,
    first: SubtypeCandidate,
    second: SubtypeCandidate,
) -> bool:
    """Return whether a slash separates two explicit same-root occupations."""

    left, right = sorted((first, second), key=lambda candidate: candidate.start)
    between = normalized_title[left.end : right.start]
    slash = re.search(r"\s/\s", between)
    if slash is None:
        return False

    slash_start = left.end + slash.start()
    slash_end = left.end + slash.end()
    earlier_slashes = list(re.finditer(r"\s/\s", normalized_title[:slash_start]))
    later_slash = re.search(r"\s/\s", normalized_title[slash_end:])
    segment_start = earlier_slashes[-1].end() if earlier_slashes else 0
    segment_end = slash_end + later_slash.start() if later_slash else len(normalized_title)
    left_segment = normalized_title[segment_start:slash_start]
    right_segment = normalized_title[slash_end:segment_end]
    evidence = ROOT_OCCUPATION_EVIDENCE.get(root, ())
    if not evidence:
        return False
    return all(
        any(re.search(pattern, segment) for pattern in evidence)
        for segment in (left_segment, right_segment)
    )


def classify_subtypes(title: str, root: str) -> tuple[str, str]:
    normalized = normalize_text(title)
    candidates = collect_subtype_candidates(normalized, root)
    if not candidates:
        return FALLBACK_SUBTYPES[root], ""

    primary = candidates[0]
    secondary = next(
        (
            candidate.label
            for candidate in candidates[1:]
            if candidate.label != primary.label
            and separator_between(normalized, root, primary, candidate)
        ),
        "",
    )
    return primary.label, secondary


def extract_tags(normalized_title: str, rules: tuple[TagRule, ...]) -> list[str]:
    return [
        rule.label
        for rule in rules
        if any(re.search(pattern, normalized_title) for pattern in rule.patterns)
    ]


def classify_row(title: str, root: str) -> dict[str, str]:
    normalized = normalize_text(title)
    primary, secondary = classify_subtypes(title, root)
    return {
        "Original Job Title": title,
        "Root Position": root,
        "Position Subtype": primary,
        "Secondary Subtype": secondary,
        "Domain Tags": ", ".join(extract_tags(normalized, DOMAIN_RULES)),
        "Technology Tags": ", ".join(extract_tags(normalized, TECHNOLOGY_RULES)),
        "Seniority": ", ".join(extract_tags(normalized, SENIORITY_RULES)),
    }


def write_job_classes(output_file: Path, rows: list[dict[str, str]]) -> None:
    """Atomically rebuild the enriched job-classification CSV."""

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


def validate_taxonomy() -> None:
    missing_fallbacks = sorted(ROOT_TAXONOMY - FALLBACK_SUBTYPES.keys())
    unknown_rule_roots = sorted(ROOT_SUBTYPE_RULES.keys() - ROOT_TAXONOMY)
    if missing_fallbacks:
        raise ValueError("Missing fallback subtype(s): " + ", ".join(missing_fallbacks))
    if unknown_rule_roots:
        raise ValueError("Rules reference unknown root(s): " + ", ".join(unknown_rule_roots))

    for root, rules in ROOT_SUBTYPE_RULES.items():
        seen_rules: set[tuple[str, tuple[str, ...], int]] = set()
        for rule in rules:
            identity = (rule.label, rule.patterns, rule.priority)
            if identity in seen_rules:
                raise ValueError(f"Duplicate subtype rule configured for root {root!r}.")
            seen_rules.add(identity)
            for pattern in rule.patterns:
                re.compile(pattern)

    for patterns in ROOT_OCCUPATION_EVIDENCE.values():
        for pattern in patterns:
            re.compile(pattern)

    for rules in (DOMAIN_RULES, TECHNOLOGY_RULES, SENIORITY_RULES):
        for rule in rules:
            for pattern in rule.patterns:
                re.compile(pattern)

    failures = []
    for title, root, expected in REGRESSION_CASES:
        actual, _ = classify_subtypes(title, root)
        if actual != expected:
            failures.append((title, root, expected, actual))
    if failures:
        details = "; ".join(
            f"{title!r} [{root}]: expected {expected}, got {actual}"
            for title, root, expected, actual in failures
        )
        raise ValueError(f"Taxonomy regression check failed: {details}")

    secondary_failures = []
    for title, root, expected in SECONDARY_REGRESSION_CASES:
        _, actual = classify_subtypes(title, root)
        if actual != expected:
            secondary_failures.append((title, root, expected, actual))
    if secondary_failures:
        details = "; ".join(
            f"{title!r} [{root}]: expected secondary {expected!r}, got {actual!r}"
            for title, root, expected, actual in secondary_failures
        )
        raise ValueError(f"Secondary-subtype regression check failed: {details}")


def print_random_sample(
    rows: list[dict[str, str]],
    sample_size: int,
    seed: int | None,
) -> None:
    if sample_size == 0 or not rows:
        return

    actual_size = min(sample_size, len(rows))
    random_source = random.Random(seed) if seed is not None else random.SystemRandom()
    sampled_rows = random_source.sample(rows, actual_size)
    print()
    print(f"RANDOM SPECIALIZATION VALIDATION SAMPLE ({actual_size} ROWS)")
    if seed is not None:
        print(f"Reproducible sample seed: {seed}")
    print("Copy the following lines for manual review:")
    print()
    for number, row in enumerate(sampled_rows, start=1):
        suffix = (
            f" / {row['Secondary Subtype']}"
            if row["Secondary Subtype"]
            else ""
        )
        print(
            f"{number:02d}. {row['Original Job Title']} >>> "
            f"{row['Root Position']} >>> {row['Position Subtype']}{suffix}"
        )


def build_job_classes(
    input_file: Path,
    output_file: Path,
    sample_size: int = 50,
    seed: int | None = None,
) -> Counter[str]:
    source_rows = read_root_classified_titles(input_file)
    output_rows = [classify_row(title, root) for title, root in source_rows]
    write_job_classes(output_file, output_rows)

    subtype_counts = Counter(row["Position Subtype"] for row in output_rows)
    fallback_count = sum(
        row["Position Subtype"] == FALLBACK_SUBTYPES[row["Root Position"]]
        for row in output_rows
    )
    secondary_count = sum(bool(row["Secondary Subtype"]) for row in output_rows)
    print(f"Read {len(source_rows)} root-classified title(s) from:")
    print(f"  {input_file}")
    print(f"Wrote {len(output_rows)} enriched classification row(s) to:")
    print(f"  {output_file}")
    print(f"Position subtypes represented: {len(subtype_counts)}")
    print(f"Fallback/general subtypes: {fallback_count}")
    print(f"Explicit secondary subtypes: {secondary_count}")
    print_random_sample(output_rows, sample_size, seed)
    return subtype_counts


def non_negative_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Expand root-classified Czech/English job titles into position "
            "subtypes and independent domain, technology, and seniority tags."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help=f"Root-classified CSV path (default: {DEFAULT_INPUT_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Enriched output CSV path (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--sample-size",
        type=non_negative_integer,
        default=200,
        help="Number of random mappings printed for review (default: 200)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional integer seed for a reproducible validation sample",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        validate_taxonomy()
        build_job_classes(
            arguments.input,
            arguments.output,
            sample_size=arguments.sample_size,
            seed=arguments.seed,
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
