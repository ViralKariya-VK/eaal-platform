"""Read-only queries for a student's progress through a Lab's stages.

Separated from ``bootstrap.py`` (which creates rows) and from the screens
(which shouldn't embed raw queries) so the unlock rule lives in exactly one
place: a stage is unlocked once the *previous* stage has at least one
submitted session. If that rule ever needs to change (e.g. requiring a
minimum score, not just a submission), this is the only place to change it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import Session, Stage


def is_stage_unlocked(
    session_factory: sessionmaker[OrmSession], student_id: int, stage: Stage
) -> bool:
    """Whether ``student_id`` may start ``stage``.

    The first stage (``order_index == 0``) is always unlocked. Any later
    stage requires a submitted session for the stage immediately before it.
    """
    if stage.order_index == 0:
        return True

    with session_factory() as db_session:
        previous_stage = (
            db_session.query(Stage)
            .filter_by(task_id=stage.task_id, order_index=stage.order_index - 1)
            .one_or_none()
        )
        if previous_stage is None:
            # No previous stage on record — fail open to unlocked rather
            # than permanently locking the student out over a data gap.
            return True

        submitted = (
            db_session.query(Session)
            .filter(
                Session.student_id == student_id,
                Session.stage_id == previous_stage.id,
                Session.submitted_at.is_not(None),
            )
            .first()
        )
        return submitted is not None


def has_submitted_stage(
    session_factory: sessionmaker[OrmSession], student_id: int, stage_id: int
) -> bool:
    """Whether ``student_id`` has ever submitted an attempt at this stage."""
    with session_factory() as db_session:
        submitted = (
            db_session.query(Session)
            .filter(
                Session.student_id == student_id,
                Session.stage_id == stage_id,
                Session.submitted_at.is_not(None),
            )
            .first()
        )
        return submitted is not None
