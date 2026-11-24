"""Flask CLI: user management.

    flask --app run create-user alice --role dispatcher
    flask --app run set-password alice
"""
import re

import click
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.db import get_write_session
from models.user import User

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,80}$")
MIN_PASSWORD_LENGTH = 12


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise click.UsageError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


@click.command("create-user")
@click.argument("username")
@click.option("--role", type=click.Choice(["dispatcher", "viewer"]), default="viewer", show_default=True)
@click.password_option(confirmation_prompt=True)
def create_user(username: str, role: str, password: str) -> None:
    """Create a user. Dispatchers can change data; viewers are read-only."""
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise click.UsageError("Username must be 3-80 characters of a-z, 0-9, '.', '_' or '-'.")
    _check_password(password)
    with get_write_session() as db:
        if db.scalar(select(User.id).where(User.username == username)):
            raise click.UsageError(f"User {username!r} already exists.")
        db.add(User(username=username, password_hash=generate_password_hash(password), role=role))
    click.echo(f"Created {role} {username!r}.")


@click.command("set-password")
@click.argument("username")
@click.password_option(confirmation_prompt=True)
def set_password(username: str, password: str) -> None:
    """Change a user's password (ends their existing sessions)."""
    _check_password(password)
    with get_write_session() as db:
        user = db.scalar(select(User).where(User.username == username.strip().lower()))
        if user is None:
            raise click.UsageError("No such user.")
        user.password_hash = generate_password_hash(password)
    click.echo("Password updated.")


def register_cli(app) -> None:
    app.cli.add_command(create_user)
    app.cli.add_command(set_password)
