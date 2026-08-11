"""
Small, shared helpers used across routes. Business logic lives in
services/, not here.
"""
import re
from datetime import datetime, timezone
from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user

from backend.config import ALLOWED_EXTENSIONS

EMAIL_VALIDATOR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email):
    return bool(EMAIL_VALIDATOR.match(email or ""))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("tickets.dashboard"))
        return view(*args, **kwargs)
    return wrapped


def parse_iso_utc(value):
    """Parses an ISO-8601 timestamp as produced by JS Date.toISOString()
    (e.g. '2026-08-03T10:15:00.000Z') into a timezone-aware UTC datetime.
    This is how delegation start/end times reach the server - the browser
    converts the person's local picker value to true UTC before the form
    submits, so no timezone math happens on the server at all. Raises
    ValueError on anything invalid."""
    if not value:
        raise ValueError("Missing date/time value.")
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

