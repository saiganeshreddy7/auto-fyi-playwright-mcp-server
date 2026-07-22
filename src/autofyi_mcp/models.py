from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class BillingMonth(BaseModel):
    """One calendar month whose repeating service interim should be allocated."""

    month: int = Field(ge=1, le=12, description="Calendar month number, January=1")
    year: int = Field(ge=2000, le=2100)


class AllocationJob(BaseModel):
    """An exact FYI job name and optional explicit allocation."""

    job_name: str = Field(min_length=1)
    amount: float | None = Field(default=None, gt=0)

    @field_validator("job_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class DirectInvoiceJob(BaseModel):
    """An exact FYI job name and optional direct-invoice amount."""

    job_name: str = Field(min_length=1)
    amount: float | None = Field(default=None, ge=0)

    @field_validator("job_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class CatalogFilter(BaseModel):
    """One allowlisted filter for the imported FYI client catalog; never raw SQL."""

    column: str = Field(min_length=1)
    operator: Literal[
        "eq",
        "not_eq",
        "contains",
        "starts_with",
        "in",
        "is_null",
        "is_not_null",
        "gt",
        "gte",
        "lt",
        "lte",
    ]
    value: Any = None


InvoiceType = Literal["Progress", "Final"]
InvoiceTheme = Literal["Standard", "Practice Ignition"]
DetailLevel = Literal["summary", "full"]
