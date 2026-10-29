"""Pydantic contracts and data schemas for SavageOps multi-agent pipeline."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TriageReport(BaseModel):
    """Output from Agent 1 (Triage) to Agent 2 (Analyst)."""

    file_path: str = Field(description="Relative path to the affected source file.")
    line_start: int = Field(description="Starting line of the problematic code block.")
    line_end: int = Field(description="Ending line of the problematic code block.")
    error_summary: str = Field(description="Summary of the runtime failure.")
    severity: Literal["P1", "P2", "P3", "P4"] = Field(description="Incident severity level.")
    severity_reason: str = Field(description="Explanation for the assigned severity.")
    evidence: list[str] = Field(description="Log snippets or trace elements supporting triage.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0.")
    applicable: bool = Field(default=True, description="Whether this is an actionable code incident.")
    repository: str | None = Field(default=None, description="Mapped GitHub repository for this service.")
    git_sha: str | None = Field(default=None, description="Deployed revision from the alert labels, when available.")


class InfoRequest(BaseModel):
    """Output from Agent 2 (Analyst) requesting additional context from Agent 1 (Triage)."""

    question: str = Field(description="Specific information or logs requested.")
    service: str | None = Field(default=None, description="Target service name if different.")
    minutes: int = Field(default=15, description="Lookback window in minutes.")


class Solution(BaseModel):
    """Represents a proposed remedy (fast fix or permanent fix)."""

    title: str = Field(description="Brief title of the solution.")
    description: str = Field(description="Explanation of what this solution does.")
    type: Literal["revert", "patch"] = Field(
        description="Whether this is a git revert or code patch."
    )
    risk: str = Field(description="Potential side-effects or operational risks.")


class AnalysisReport(BaseModel):
    """Output from Agent 2 (Analyst) to Agent 3 (Spokesperson & Fixer)."""

    root_cause: str = Field(description="Detailed root cause explanation tied to lines of code.")
    culprit_sha: str | None = Field(
        default=None, description="Commit SHA that introduced the bug, if proven."
    )
    introduced_by: str = Field(
        default="unknown", description="Author and commit message if proven, else 'unknown'."
    )
    fast_fix: Solution = Field(description="Immediate containment fix (stops the damage).")
    permanent_fix: Solution = Field(description="Proper long-term code-level fix.")
    recommendation: Literal["fast", "permanent"] = Field(description="Recommended solution choice.")


class AnalystResult(BaseModel):
    """Agent 2 returns either a completed analysis or an info request back to Agent 1."""

    analysis: AnalysisReport | None = None
    info_request: InfoRequest | None = None

    @model_validator(mode="after")
    def require_exactly_one_result(self):
        if (self.analysis is None) == (self.info_request is None):
            raise ValueError("Exactly one of analysis or info_request must be provided")
        return self


class SavageSummary(BaseModel):
    """Output from Agent 3 (Spokesperson) presented to user at Gate 1."""

    roast: str = Field(
        description="Exactly 2 lines roasting the code pattern, never the individual."
    )
    explanation: str = Field(description="Plain-language root cause explanation (2-3 sentences).")
    cost_impact: str = Field(description="Estimated burn rate with explicit assumptions.")
    options: str = Field(description="One-line summary for fast fix vs permanent fix with risks.")


class PatchHunk(BaseModel):
    """One exact source replacement; old_text must occur exactly once."""

    old_text: str = Field(min_length=1)
    new_text: str = Field(min_length=1)


class FixedFile(BaseModel):
    """Output from Agent 3 (Fixer) representing exact, uniquely matched edits."""

    hunks: list[PatchHunk] = Field(min_length=1, description="Exact search/replace edits to apply in order.")
    commit_message: str = Field(description="Git commit message describing the fix.")
