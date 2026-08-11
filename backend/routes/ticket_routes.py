import os
import uuid

from flask import (
    Blueprint, flash, redirect, render_template, request,
    send_from_directory, url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

import backend.config as config
from backend.extensions import db
from backend.models import Attachment, Comment, Notification, Ticket, User, UserDelegation, utcnow, to_aware
from backend.services import ticket_service, delegation_service
from backend.utils import allowed_file, parse_iso_utc

tickets_bp = Blueprint("tickets", __name__)


@tickets_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("tickets.dashboard"))
    return redirect(url_for("auth.login"))


@tickets_bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.is_admin:
        own_query = Ticket.query
    else:
        own_query = Ticket.query.filter(
            (Ticket.created_by_id == current_user.id)
            | (Ticket.assigned_to_id == current_user.id)
            | ((Ticket.assigned_to_id.is_(None)) & (Ticket.assigned_department == current_user.department))
        )
    tickets = own_query.all()
    for t in tickets:
        ticket_service.maybe_auto_close(t)
    db.session.commit()

    stats = ticket_service.ticket_stats(tickets)
    tickets.sort(key=ticket_service.ticket_sort_key)
    recent = tickets[:8]

    covered_ids = delegation_service.delegators_covered_by(current_user.id)
    delegated_recent = []
    if covered_ids:
        delegated = Ticket.query.filter(
            (Ticket.created_by_id.in_(covered_ids)) | (Ticket.assigned_to_id.in_(covered_ids))
        ).all()
        for t in delegated:
            ticket_service.maybe_auto_close(t)
        db.session.commit()
        delegated.sort(key=ticket_service.ticket_sort_key)
        for t in delegated:
            t.delegation_owner = ticket_service.delegation_owner_name(t, covered_ids)
        delegated_recent = delegated[:8]

    return render_template(
        "dashboard.html", stats=stats, recent=recent, delegated_recent=delegated_recent,
        statuses=config.STATUSES, priorities=config.PRIORITIES,
    )


@tickets_bp.route("/tickets")
@login_required
def index():
    status_filter = request.args.get("status", "all")
    priority_filter = request.args.get("priority", "all")
    category_filter = request.args.get("category", "all")
    department_filter = request.args.get("department", "all")
    query_text = (request.args.get("q") or "").strip()
    scope = request.args.get("scope", "mine" if not current_user.is_admin else "all")

    covered_ids = delegation_service.delegators_covered_by(current_user.id)

    if scope == "department":
        query = Ticket.query.filter(
            Ticket.assigned_department == current_user.department,
            Ticket.assigned_to_id.is_(None),
        )
    elif scope == "all" and current_user.is_admin:
        query = Ticket.query
    elif scope == "delegated":
        if covered_ids:
            query = Ticket.query.filter(
                (Ticket.created_by_id.in_(covered_ids)) | (Ticket.assigned_to_id.in_(covered_ids))
            )
        else:
            query = Ticket.query.filter(Ticket.id.is_(None))
    else:
        scope = "mine"
        query = Ticket.query.filter(
            (Ticket.created_by_id == current_user.id) | (Ticket.assigned_to_id == current_user.id)
        )

    all_scoped = query.all()
    for t in all_scoped:
        ticket_service.maybe_auto_close(t)
    db.session.commit()

    counts = {}
    for t in all_scoped:
        counts[t.status] = counts.get(t.status, 0) + 1
    total = len(all_scoped)

    filtered = all_scoped
    if status_filter != "all":
        filtered = [t for t in filtered if t.status == status_filter]
    if priority_filter != "all":
        filtered = [t for t in filtered if t.priority == priority_filter]
    if category_filter != "all":
        filtered = [t for t in filtered if t.category == category_filter]
    if scope == "all" and department_filter != "all":
        filtered = [
            t for t in filtered
            if (t.assigned_to.department if t.assigned_to else t.assigned_department) == department_filter
        ]
    if query_text:
        needle = query_text.lower()
        filtered = [
            t for t in filtered
            if needle in t.ticket_number.lower() or needle in t.title.lower()
        ]
    filtered.sort(key=ticket_service.ticket_sort_key)

    if scope == "delegated":
        for t in filtered:
            t.delegation_owner = ticket_service.delegation_owner_name(t, covered_ids)

    return render_template(
        "tickets/index.html",
        tickets=filtered,
        statuses=config.STATUSES,
        status_labels=config.STATUS_LABELS,
        priorities=config.PRIORITIES,
        priority_labels=config.PRIORITY_LABELS,
        categories=config.CATEGORIES,
        departments=config.DEPARTMENTS,
        current_status=status_filter,
        current_priority=priority_filter,
        current_category=category_filter,
        current_department=department_filter,
        query_text=query_text,
        current_scope=scope,
        counts=counts,
        total=total,
        show_delegated_tab=bool(covered_ids),
    )


@tickets_bp.route("/tickets/new", methods=["GET", "POST"])
@login_required
def new_ticket():
    employees = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    employees_json = ticket_service.employees_for_picker(employees)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        priority = request.form.get("priority", "medium")
        category = request.form.get("category", "other")
        assign_mode = request.form.get("assign_mode", "person")
        assigned_to_id = request.form.get("assigned_to_id") or None
        assigned_department = request.form.get("assigned_department") or None

        form_kwargs = dict(
            priorities=config.PRIORITIES, priority_labels=config.PRIORITY_LABELS,
            sla_labels=config.SLA_LABELS, categories=config.CATEGORIES,
            departments=config.DEPARTMENTS, employees_json=employees_json, form=request.form,
            assign_mode=assign_mode,
        )

        if not title or not description:
            flash("Title and description are required.", "error")
            return render_template("tickets/new.html", **form_kwargs)
        if priority not in config.PRIORITIES:
            priority = "medium"
        if category not in config.CATEGORIES:
            category = "other"

        if assign_mode == "department":
            assigned_to_id = None
        else:
            assigned_department = None

        try:
            ticket = ticket_service.create_ticket(
                current_user, title, description, category, priority,
                int(assigned_to_id) if assigned_to_id else None,
                assigned_department,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("tickets/new.html", **form_kwargs)

        flash("Ticket created successfully.", "success")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    return render_template(
        "tickets/new.html",
        priorities=config.PRIORITIES, priority_labels=config.PRIORITY_LABELS,
        sla_labels=config.SLA_LABELS, categories=config.CATEGORIES,
        departments=config.DEPARTMENTS, employees_json=employees_json, form={},
        assign_mode="person",
    )


@tickets_bp.route("/tickets/<int:ticket_id>")
@login_required
def detail(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("tickets.index"))
    if not ticket_service.can_view_ticket(current_user, ticket):
        flash("You do not have access to this ticket.", "error")
        return redirect(url_for("tickets.index"))

    ticket_service.maybe_auto_close(ticket)
    ticket_service.maybe_auto_open(ticket, current_user)
    db.session.commit()

    employees = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    employees_json = ticket_service.employees_for_picker(employees)
    history = sorted(ticket.history, key=lambda h: h.created_at, reverse=True)
    can_claim = bool(
        ticket.assigned_to_id is None and ticket.assigned_department
        and (current_user.is_admin or current_user.department == ticket.assigned_department)
    )

    return render_template(
        "tickets/detail.html",
        ticket=ticket,
        comments=sorted(ticket.comments, key=lambda c: c.created_at),
        attachments=ticket.attachments,
        history=history,
        statuses=config.STATUSES,
        status_labels=config.STATUS_LABELS,
        status_descriptions=config.STATUS_DESCRIPTIONS,
        priorities=config.PRIORITIES,
        priority_labels=config.PRIORITY_LABELS,
        sla_labels=config.SLA_LABELS,
        departments=config.DEPARTMENTS,
        employees_json=employees_json,
        can_claim=can_claim,
        can_update_status=ticket_service.can_update_status(current_user, ticket),
        valid_transitions=config.VALID_TRANSITIONS,
    )


@tickets_bp.route("/tickets/<int:ticket_id>/update", methods=["POST"])
@login_required
def update(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not ticket_service.can_view_ticket(current_user, ticket):
        flash("Access denied.", "error")
        return redirect(url_for("tickets.index"))

    data = {}
    status = request.form.get("status")
    if status and status != ticket.status:
        data["status"] = status
    priority = request.form.get("priority")
    if current_user.is_admin and priority and priority != ticket.priority:
        data["priority"] = priority
    if current_user.is_admin:
        assigned_to_id = request.form.get("assigned_to_id")
        clear_assignment = request.form.get("clear_assignment")
        assigned_department = request.form.get("assigned_department")
        if assigned_to_id:
            data["assigned_to_id"] = int(assigned_to_id)
        elif assigned_department:
            data["assigned_department"] = assigned_department
        elif clear_assignment:
            data["assigned_to_id"] = None
            data["assigned_department"] = None

    if not data:
        flash("No changes to save.", "info")
        return redirect(url_for("tickets.detail", ticket_id=ticket_id))

    try:
        ticket_service.update_ticket(current_user, ticket, data)
        flash("Ticket updated.", "success")
    except (ValueError, PermissionError) as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("tickets.detail", ticket_id=ticket_id))


@tickets_bp.route("/tickets/<int:ticket_id>/claim", methods=["POST"])
@login_required
def claim(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("tickets.index"))
    try:
        ticket_service.claim_ticket(current_user, ticket)
        flash("Ticket claimed - it's yours now.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("tickets.detail", ticket_id=ticket_id))


@tickets_bp.route("/tickets/<int:ticket_id>/comment", methods=["POST"])
@login_required
def add_comment(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not ticket_service.can_view_ticket(current_user, ticket):
        flash("Access denied.", "error")
        return redirect(url_for("tickets.index"))

    body = request.form.get("body", "").strip()
    if not body:
        flash("Comment cannot be empty.", "error")
        return redirect(url_for("tickets.detail", ticket_id=ticket_id))

    db.session.add(Comment(ticket_id=ticket.id, user_id=current_user.id, body=body))
    ticket_service.log_history(ticket.id, current_user.id, "comment_added")
    for stakeholder_id in ticket_service.get_ticket_stakeholders(ticket):
        if stakeholder_id != current_user.id:
            ticket_service.notify(stakeholder_id, ticket.id, "New comment",
                                   f"{current_user.full_name} commented on {ticket.ticket_number}")
    ticket.updated_at = utcnow()
    db.session.commit()
    flash("Comment added.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket_id))


@tickets_bp.route("/tickets/<int:ticket_id>/attachments", methods=["POST"])
@login_required
def upload_attachment(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not ticket_service.can_view_ticket(current_user, ticket):
        flash("Access denied.", "error")
        return redirect(url_for("tickets.index"))

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("tickets.detail", ticket_id=ticket_id))
    if not allowed_file(file.filename):
        flash("File type not allowed.", "error")
        return redirect(url_for("tickets.detail", ticket_id=ticket_id))

    original_name = secure_filename(file.filename)
    stored_name = f"{uuid.uuid4().hex}_{original_name}"
    file.save(os.path.join(config.UPLOAD_FOLDER, stored_name))

    db.session.add(Attachment(
        ticket_id=ticket.id, uploaded_by_id=current_user.id,
        file_name=original_name, stored_file_name=stored_name,
    ))
    for stakeholder_id in ticket_service.get_ticket_stakeholders(ticket):
        if stakeholder_id != current_user.id:
            ticket_service.notify(stakeholder_id, ticket.id, "New attachment",
                                   f"{current_user.full_name} uploaded a file to {ticket.ticket_number}")
    ticket.updated_at = utcnow()
    db.session.commit()
    flash("File uploaded.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket_id))


@tickets_bp.route("/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(attachment_id):
    attachment = db.session.get(Attachment, attachment_id)
    if not attachment:
        flash("File not found.", "error")
        return redirect(url_for("tickets.index"))
    ticket = db.session.get(Ticket, attachment.ticket_id)
    if not ticket_service.can_view_ticket(current_user, ticket):
        flash("Access denied.", "error")
        return redirect(url_for("tickets.index"))
    return send_from_directory(
        config.UPLOAD_FOLDER, attachment.stored_file_name,
        as_attachment=True, download_name=attachment.file_name,
    )


@tickets_bp.route("/tickets/<int:ticket_id>/delete", methods=["POST"])
@login_required
def delete_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("tickets.index"))
    if not current_user.is_admin:
        flash("Only administrators can delete tickets.", "error")
        return redirect(url_for("tickets.detail", ticket_id=ticket_id))
    db.session.delete(ticket)
    db.session.commit()
    flash("Ticket deleted.", "success")
    return redirect(request.referrer or url_for("tickets.index"))


@tickets_bp.route("/notifications")
@login_required
def notifications():
    items = (
        Notification.query.filter_by(user_id=current_user.id, is_read=False)
        .order_by(Notification.created_at.desc()).all()
    )
    return render_template("notifications.html", notifications=items)


@tickets_bp.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    notification = db.session.get(Notification, notification_id)
    if not notification or notification.user_id != current_user.id:
        flash("Notification not found.", "error")
        return redirect(url_for("tickets.dashboard"))
    notification.is_read = True
    db.session.commit()
    if notification.ticket_id:
        return redirect(url_for("tickets.detail", ticket_id=notification.ticket_id))
    return redirect(url_for("tickets.dashboard"))


@tickets_bp.route("/notifications/<int:notification_id>/dismiss", methods=["POST"])
@login_required
def dismiss_notification(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification and notification.user_id == current_user.id:
        notification.is_read = True
        db.session.commit()
    return redirect(request.referrer or url_for("tickets.dashboard"))


@tickets_bp.route("/notifications/dismiss-all", methods=["POST"])
@login_required
def dismiss_all_notifications():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
        {"is_read": True}, synchronize_session=False
    )
    db.session.commit()
    return redirect(request.referrer or url_for("tickets.dashboard"))


# --- Profile / delegation management ---------------------------------

@tickets_bp.route("/profile")
@login_required
def profile():
    employees = (
        User.query.filter(User.is_active.is_(True), User.id != current_user.id)
        .order_by(User.full_name).all()
    )
    employees_json = ticket_service.employees_for_picker(employees)
    my_delegations = (
        UserDelegation.query.filter_by(delegator_id=current_user.id)
        .order_by(UserDelegation.created_at.desc()).all()
    )
    received_delegations = (
        UserDelegation.query.filter_by(delegatee_id=current_user.id)
        .order_by(UserDelegation.created_at.desc()).all()
    )
    return render_template(
        "profile.html",
        employees=employees,
        employees_json=employees_json,
        my_delegations=my_delegations,
        received_delegations=received_delegations,
    )


@tickets_bp.route("/profile/delegations", methods=["POST"])
@login_required
def create_delegation():
    delegatee_id = request.form.get("delegatee_id")
    try:
        start_time = parse_iso_utc(request.form.get("start_time"))
        end_time = parse_iso_utc(request.form.get("end_time"))
    except (ValueError, TypeError):
        flash("Please provide a valid start and end date/time.", "error")
        return redirect(url_for("tickets.profile"))

    try:
        delegation_service.create_delegation(current_user, delegatee_id, start_time, end_time)
        flash("Delegation created.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("tickets.profile"))


@tickets_bp.route("/profile/delegations/<int:delegation_id>/edit", methods=["GET", "POST"])
@login_required
def edit_delegation(delegation_id):
    delegation = db.session.get(UserDelegation, delegation_id)
    if not delegation or delegation.delegator_id != current_user.id:
        flash("Delegation not found.", "error")
        return redirect(url_for("tickets.profile"))
    if delegation.status_label() not in ("pending", "active"):
        flash("Only a pending or active delegation can be edited.", "error")
        return redirect(url_for("tickets.profile"))

    if request.method == "POST":
        delegatee_id = request.form.get("delegatee_id")
        try:
            start_time = parse_iso_utc(request.form.get("start_time"))
            end_time = parse_iso_utc(request.form.get("end_time"))
        except (ValueError, TypeError):
            flash("Please provide a valid start and end date/time.", "error")
            return redirect(url_for("tickets.edit_delegation", delegation_id=delegation_id))
        try:
            delegation_service.update_delegation(current_user, delegation_id, delegatee_id, start_time, end_time)
            flash("Delegation updated.", "success")
            return redirect(url_for("tickets.profile"))
        except (ValueError, PermissionError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("tickets.edit_delegation", delegation_id=delegation_id))

    employees = (
        User.query.filter(User.is_active.is_(True), User.id != current_user.id)
        .order_by(User.full_name).all()
    )
    employees_json = ticket_service.employees_for_picker(employees)
    return render_template(
        "delegation_edit.html",
        delegation=delegation,
        employees_json=employees_json,
        start_iso=to_aware(delegation.start_time).isoformat(),
        end_iso=to_aware(delegation.end_time).isoformat(),
        back_url=url_for("tickets.profile"),
        submit_url=url_for("tickets.edit_delegation", delegation_id=delegation_id),
    )


@tickets_bp.route("/profile/delegations/<int:delegation_id>/revoke", methods=["POST"])
@login_required
def revoke_delegation(delegation_id):
    try:
        delegation_service.revoke_delegation(current_user, delegation_id)
        flash("Delegation revoked.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("tickets.profile"))
