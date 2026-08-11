from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from backend.extensions import db
from backend.models import User
from backend.services import message_service, ticket_service

messages_bp = Blueprint("messages", __name__, url_prefix="/connections")


@messages_bp.route("/")
@login_required
def inbox():
    threads = message_service.inbox_threads(current_user)
    employees = (
        User.query.filter(User.is_active.is_(True), User.id != current_user.id)
        .order_by(User.full_name).all()
    )
    employees_json = ticket_service.employees_for_picker(employees)
    return render_template(
        "connections/inbox.html", threads=threads, employees_json=employees_json,
    )


@messages_bp.route("/<int:other_id>")
@login_required
def thread(other_id):
    other = db.session.get(User, other_id)
    if not other:
        flash("That colleague was not found.", "error")
        return redirect(url_for("messages.inbox"))

    message_service.mark_thread_read(current_user, other)
    history = message_service.thread_with(current_user, other)
    threads = message_service.inbox_threads(current_user)
    employees = (
        User.query.filter(User.is_active.is_(True), User.id != current_user.id)
        .order_by(User.full_name).all()
    )
    employees_json = ticket_service.employees_for_picker(employees)
    return render_template(
        "connections/thread.html", other=other, history=history,
        threads=threads, employees_json=employees_json,
    )


@messages_bp.route("/send", methods=["POST"])
@login_required
def send():
    recipient_id = request.form.get("recipient_id")
    body = request.form.get("body", "")
    if not recipient_id:
        flash("Pick a colleague to message.", "error")
        return redirect(url_for("messages.inbox"))
    try:
        message_service.send_message(current_user, int(recipient_id), body)
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("messages.thread", other_id=recipient_id))

