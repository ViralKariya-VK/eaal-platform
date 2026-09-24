"""The one pilot-specific table this survey needs, beyond what
``eaal_platform.db.models`` already provides.

Deliberately its own tiny declarative base rather than added to the core
schema: consent timestamps and a self-rated score-plausibility question are
specific to *this* pilot instrument, not a general CAVY feature, and don't
belong in the app's own data model. It lives in the same SQLite file as
the core tables (nothing stops two declarative bases from sharing a
database), so a `Student`/`Session` row and its `PilotResponse` are still
trivially joinable by `student_id`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class PilotBase(DeclarativeBase):
    """Separate declarative base — see module docstring."""


class PilotResponse(PilotBase):
    """One participant's consent record and (once they finish) self-rating.

    ``self_rating`` is a 1-5 Likert item ("how did the computed score
    compare to what you expected"), not a validated instrument — it is
    face-validity evidence at best (self-perception, subject to all the
    usual self-assessment biases), reported as such in any writeup.
    """

    __tablename__ = "pilot_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    task1_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task2_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    self_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    self_rating_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ciq_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
