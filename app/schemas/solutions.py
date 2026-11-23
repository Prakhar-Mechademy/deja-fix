"""Solution extraction and verification schemas."""

from pydantic import BaseModel, Field


class ExtractedSolution(BaseModel):
    problem: str = Field(min_length=1)
    symptoms: list[str] = Field(default_factory=list)
    device: str | None = None
    operating_system: str | None = None
    app_version: str | None = None
    fix_steps: list[str] = Field(default_factory=list)
    failed_attempts: list[str] = Field(default_factory=list)


class ExtractedSolutionSet(BaseModel):
    solutions: list[ExtractedSolution] = Field(default_factory=list)
