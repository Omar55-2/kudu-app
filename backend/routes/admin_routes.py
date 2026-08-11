from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import backend.config as config
from backend.extensions import db
from backend.models import Ticket, User, UserDelegation
from backend.services import delegation_service
from backend.utils import admin_required
from backend.utils import admin_required, parse_iso_utc

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@login_required
@admin_required
def panel():
    query_text = (request.args.get("q") or "").strip()
    department_filter = request.args.get("department", "all")
    role_filter = request.args.get("role", "all")
    status_filter = request.args.get("status", "all")
    try:
        per_page = int(request.args.get("per_page", 10))
    except ValueError:
        per_page = 10
    if per_page not in (10, 25, 50):
        per_page = 10
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    query = User.query
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(db.or_(User.full_name.ilike(like), User.email.ilike(like)))
    if department_filter != "all":
        query = query.filter(User.department == department_filter)
    if role_filter != "all":
        query = query.filter(User.role == role_filter)
    if status_filter == "active":
        query = query.filter(User.is_active.is_(True))
    elif status_filter == "inactive":
        query = query.filter(User.is_active.is_(False))
    elif status_filter == "unverified":
        query = query.filter(User.email_verified.is_(False))

    query = query.order_by(User.created_at.desc())
    total_users = query.count()
    total_pages = max((total_users + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    users = query.offset((page - 1) * per_page).limit(per_page).all()

    tickets = Ticket.query.order_by(Ticket.created_at.desc()).limit(10).all()

    return render_template(
        "admin/panel.html",
        users=users, tickets=tickets,
        departments=config.DEPARTMENTS, status_labels=config.STATUS_LABELS,
        query_text=query_text, department_filter=department_filter,
        role_filter=role_filter, status_filter=status_filter,
        page=page, per_page=per_page, total_pages=total_pages, total_users=total_users,
    )


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.panel"))
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin.panel"))
    user.is_active = not user.is_active
    db.session.commit()
    state = "activated" if user.is_active else "deactivated"
    flash(f"{user.full_name} was {state}.", "success")
    return redirect(url_for("admin.panel"))


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def change_role(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.panel"))
    role = request.form.get("role")
    if role not in config.ROLES:
        flash("Invalid role.", "error")
        return redirect(url_for("admin.panel"))
    if user.id == current_user.id and role != "admin":
        flash("You cannot remove your own admin role.", "error")
        return redirect(url_for("admin.panel"))
    user.role = role
    db.session.commit()
    flash(f"Role updated for {user.full_name}.", "success")
    return redirect(url_for("admin.panel"))


@admin_bp.route("/users/<int:user_id>/department", methods=["POST"])
@login_required
@admin_required
def change_department(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.panel"))
    department = request.form.get("department")
    if department not in config.DEPARTMENTS:
        flash("Invalid department.", "error")
        return redirect(url_for("admin.panel"))
    user.department = department
    db.session.commit()
    flash(f"Department updated for {user.full_name}.", "success")
    return redirect(url_for("admin.panel"))

@admin_bp.route("/delegations")
@login_required
@admin_required
def delegations():
    all_delegations = UserDelegation.query.order_by(UserDelegation.created_at.desc()).all()
    return render_template("admin/delegations.html", delegations=all_delegations)


@admin_bp.route("/delegations/<int:delegation_id>/revoke", methods=["POST"])
@login_required
@admin_required
def revoke_delegation(delegation_id):
    try:
        delegation_service.revoke_delegation(current_user, delegation_id)
        flash("Delegation revoked.", "success")
    except (ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.delegations"))


@admin_bp.route("/delegations/<int:delegation_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_delegation(delegation_id):
    delegation = db.session.get(UserDelegation, delegation_id)
    if not delegation:
        flash("Delegation not found.", "error")
        return redirect(url_for("admin.delegations"))
    if delegation.status_label() not in ("pending", "active"):
        flash("Only a pending or active delegation can be edited.", "error")
        return redirect(url_for("admin.delegations"))

    if request.method == "POST":
        delegatee_id = request.form.get("delegatee_id")
        try:
            start_time = parse_iso_utc(request.form.get("start_time"))
            end_time = parse_iso_utc(request.form.get("end_time"))
        except (ValueError, TypeError):
            flash("Please provide a valid start and end date/time.", "error")
            return redirect(url_for("admin.edit_delegation", delegation_id=delegation_id))
        try:
            delegation_service.update_delegation(current_user, delegation_id, delegatee_id, start_time, end_time)
            flash("Delegation updated.", "success")
            return redirect(url_for("admin.delegations"))
        except (ValueError, PermissionError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.edit_delegation", delegation_id=delegation_id))

    employees = (
        User.query.filter(User.is_active.is_(True), User.id != delegation.delegator_id)
        .order_by(User.full_name).all()
    )
    from backend.services import ticket_service
    employees_json = ticket_service.employees_for_picker(employees)
    return render_template(
        "delegation_edit.html",
        delegation=delegation,
        employees_json=employees_json,
        start_iso=delegation.start_time.isoformat(),
        end_iso=delegation.end_time.isoformat(),
        back_url=url_for("admin.delegations"),
        submit_url=url_for("admin.edit_delegation", delegation_id=delegation_id),
    )
