"""Agent 3 Part A: Spokesperson Agent generating brutal, funny incident summaries for Gate 1."""

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from savageops.config import OPENAI_MODEL
from savageops.db import log_run
from savageops.models import SavageSummary

SYSTEM = """You are SavageOps: a brutally honest, funny senior DevOps engineer.
Summarise the incident for the developer.
- roast: exactly 2 lines. Roast the CODE and the PATTERN, never the person.
  No slurs, no personal attacks.
- explanation: plain-language root cause, 2-3 sentences, accurate to the report.
- cost_impact: an ESTIMATED monthly AWS burn rate. Show your assumptions
  (requests/day, instance type, retries) and mark it clearly as an estimate.
- options: fast fix vs permanent fix, one line each, with the risk.
Stay faithful to the analysis. Do not invent facts."""

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM),
        ("human", "TRIAGE:\n{triage}\n\nANALYSIS:\n{analysis}"),
    ]
)


def get_llm():
    return ChatOpenAI(model=OPENAI_MODEL, temperature=0.7).with_structured_output(SavageSummary)


def run_spokesperson(incident_id: str, triage: dict, analysis: dict) -> SavageSummary:
    """Generate the savage human-facing incident summary for Gate 1 review."""
    llm = get_llm()
    summary: SavageSummary = (prompt | llm).invoke(
        {"triage": str(triage), "analysis": str(analysis)}
    )
    log_run(incident_id, "spokesperson", "output", None, summary.model_dump())
    return summary
