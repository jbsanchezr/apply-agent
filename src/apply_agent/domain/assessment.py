"""The agent's judgement about one email, as a validated domain object."""

from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from apply_agent.domain.enums import MessageCategory


def _single_line(value: str) -> str:
    return " ".join(value.split())


class MessageAssessment(BaseModel):
    """What one email means for the user's job applications."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    is_job_application: bool = Field(
        description="True only if the email concerns an application the user has made."
    )
    category: MessageCategory
    company: Annotated[str, Field(min_length=1, max_length=200)] | None = Field(
        description="The hiring company (not an agency or platform). Null if not job-related."
    )
    role: Annotated[str, Field(min_length=1, max_length=200)] | None = Field(
        description="Job title as written in the email, or null if not stated."
    )
    summary: Annotated[str, AfterValidator(_single_line), Field(min_length=1, max_length=200)] = (
        Field(description="One line in English stating what happened.")
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.category is not MessageCategory.OTHER and not self.is_job_application:
            raise ValueError(f"category {self.category.value!r} requires is_job_application=true")
        if self.is_job_application and self.company is None:
            raise ValueError("company is required when is_job_application is true")
        if not self.is_job_application and self.company is not None:
            raise ValueError("company must be null when is_job_application is false")
        return self
