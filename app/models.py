"""Pydantic schemas shared by the API, the lookup providers and the database layer."""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Status = Literal["owned", "sample", "decant", "wishlist", "tried", "sold"]

# -2 hate, -1 dislike, 0 neutral, 1 like, 2 love
SENTIMENT_LABELS = {-2: "Hate", -1: "Dislike", 0: "Neutral", 1: "Like", 2: "Love"}


class Accord(BaseModel):
    name: str
    strength: float = Field(1.0, description="Relative strength 0-1 (Fragrantica's accord bar width)")

    @field_validator("strength")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class FragranceDetails(BaseModel):
    """Everything we know about a fragrance from the web (no personal data)."""

    name: str
    brand: str = ""
    year: Optional[int] = None
    concentration: Optional[str] = None
    gender: Optional[str] = None
    family: Optional[str] = None
    perfumers: list[str] = []
    accords: list[Accord] = []
    top_notes: list[str] = []
    middle_notes: list[str] = []
    base_notes: list[str] = []
    notes: list[str] = Field([], description="Notes when the pyramid is flat (no top/middle/base split)")
    description: Optional[str] = None
    longevity: Optional[str] = None
    sillage: Optional[str] = None
    seasons: list[str] = []
    community_rating: Optional[float] = None
    rating_votes: Optional[int] = None
    image_url: Optional[str] = None
    source_url: Optional[str] = None
    source: Optional[str] = None  # "fragrantica" | "claude" | "manual"

    def all_notes(self) -> list[str]:
        return [*self.top_notes, *self.middle_notes, *self.base_notes, *self.notes]


class Alternative(BaseModel):
    label: str
    url: str


class LookupResult(BaseModel):
    details: FragranceDetails
    alternatives: list[Alternative] = []
    message: Optional[str] = None
    prediction: Optional[dict] = None
    duplicate_of: Optional[int] = None


class LookupRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)


class PersonalFields(BaseModel):
    status: Status = "owned"
    sentiment: Optional[int] = Field(None, ge=-2, le=2)
    bottle_ml: Optional[float] = Field(None, ge=0)
    personal_notes: Optional[str] = None


class FragranceCreate(FragranceDetails, PersonalFields):
    allow_duplicate: bool = False


class QuickAdd(PersonalFields):
    query: str = Field(..., min_length=2, max_length=500)


class FragranceUpdate(BaseModel):
    """Partial update: only fields that are sent are changed."""

    name: Optional[str] = None
    brand: Optional[str] = None
    year: Optional[int] = None
    concentration: Optional[str] = None
    gender: Optional[str] = None
    family: Optional[str] = None
    perfumers: Optional[list[str]] = None
    accords: Optional[list[Accord]] = None
    top_notes: Optional[list[str]] = None
    middle_notes: Optional[list[str]] = None
    base_notes: Optional[list[str]] = None
    notes: Optional[list[str]] = None
    description: Optional[str] = None
    longevity: Optional[str] = None
    sillage: Optional[str] = None
    seasons: Optional[list[str]] = None
    image_url: Optional[str] = None
    source_url: Optional[str] = None
    status: Optional[Status] = None
    sentiment: Optional[int] = Field(None, ge=-2, le=2)
    bottle_ml: Optional[float] = Field(None, ge=0)
    personal_notes: Optional[str] = None


class WearCreate(BaseModel):
    fragrance_id: int
    worn_on: date = Field(default_factory=date.today)
    occasion: Optional[str] = None
    weather: Optional[str] = None
    sprays: Optional[int] = Field(None, ge=0, le=50)
    enjoyment: Optional[int] = Field(None, ge=-2, le=2)
    compliments: int = Field(0, ge=0)
    notes: Optional[str] = None
