"""Model package.

Deliberately empty of imports so that pure modules (models.constants, used by the
state machine and the AI validation layer) can be imported without SQLAlchemy.
Use `models.register_all()` to make sure every table is on Base.metadata.
"""


def register_all() -> None:
    import models.transit  # noqa: F401
    import models.user  # noqa: F401
