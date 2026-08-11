from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

import backend.config as config
from backend.extensions import db
from backend.models import User, Ticket
from backend.services import team_service, delegation_service, ticket_service
from backend.utils import admin_required

team_bp = Blueprint("team", __name__, url_prefix="/team")


@team_bp.route("/")
@login_required
def overview():
    stats = team_service.team_overview_stats()

    workloads = []
    for m in User.query.filter_by(is_active=True).order_by(User.full_name).all():
        w = team_service.user_workload(m)
        w["user"] = m
        workloads.append(w)
    workloads.sort(key=lambda w: w["pct"], reverse=True)

    alerts = team_service.team_alerts() if current_user.is_admin else []
    return render_template("team/overview.html", stats=stats, workloads=workloads, alerts=alerts)


@team_bp.route("/members")
@login_required
def members():
    query_text = (request.args.get("q") or "").strip()
    department_filter = request.args.get("department", "all")
    availability_filter = request.args.get("availability", "all")
    workload_filter = request.args.get("workload", "all")

    query = User.query.filter_by(is_active=True)
    if query_text:
        like = f"%{query_text}%"
        query = query.filter(db.or_(User.full_name.ilike(like), User.email.ilike(like)))
    if department_filter != "all":
        query = query.filter(User.department == department_filter)
    if availability_filter != "all":
        query = query.filter(User.availability == availability_filter)

    people = []
    # for m in query.order_by(User.full_name).all():
    #     w = team_service.user_workload(m)
    #     if workload_filter != "all" and w["state"] != workload_filter:
    #         continue
    #     people.append({"user": m, "workload": w})
    for m in query.order_by(User.full_name).all():
        w = team_service.user_workload(m)
        eff_avail = team_service.effective_availability(m)
        if availability_filter != "all" and eff_avail != availability_filter:
            continue
        if workload_filter != "all" and w["state"] != workload_filter:
            continue
        people.append({"user": m, "workload": w, "effective_availability": eff_avail})
    
    
    
    return render_template(
        "team/members.html", people=people, departments=config.DEPARTMENTS,
        query_text=query_text, department_filter=department_filter,
        availability_filter=availability_filter, workload_filter=workload_filter,
        availability_labels=config.AVAILABILITY_LABELS,
        workload_states=[s[2] for s in config.WORKLOAD_STATES],
    )


@team_bp.route("/members/<int:user_id>")
@login_required
def member_profile(user_id):
    member = db.session.get(User, user_id)
    if not member:
        flash("Team member not found.", "error")
        return redirect(url_for("team.members"))

    workload = team_service.user_workload(member)
    covering = delegation_service.active_delegation_for(member.id)
    covered_by = delegation_service.active_delegations_received_by(member.id)

    candidate_tickets = (
        Ticket.query.filter(Ticket.assigned_to_id == member.id)
        .order_by(Ticket.updated_at.desc()).limit(20).all()
    )
    tickets = [t for t in candidate_tickets if ticket_service.can_view_ticket(current_user, t)][:8]

    performance = team_service.sla_performance(member) if current_user.is_admin else None

    return render_template(
        "team/member_profile.html", member=member, workload=workload,
        covering=covering, covered_by=covered_by, tickets=tickets,
        performance=performance, departments=config.DEPARTMENTS,
        availability_labels=config.AVAILABILITY_LABELS,
        availability_states=config.AVAILABILITY_STATES,
    )


@team_bp.route("/members/<int:user_id>/availability", methods=["POST"])
@login_required
@admin_required
def change_availability(user_id):
    member = db.session.get(User, user_id)
    if not member:
        flash("Team member not found.", "error")
        return redirect(url_for("team.members"))
    availability = request.form.get("availability")
    if availability not in config.AVAILABILITY_STATES:
        flash("Invalid availability.", "error")
    else:
        member.availability = availability
        db.session.commit()
        flash(f"{member.full_name}'s availability updated.", "success")
    return redirect(url_for("team.member_profile", user_id=user_id))


@team_bp.route("/departments")
@login_required
def departments():
    dept_stats = [team_service.department_stats(d) for d in config.DEPARTMENTS]
    return render_template("team/departments.html", dept_stats=dept_stats)


@team_bp.route("/departments/<department>")
@login_required
def department_detail(department):
    if department not in config.DEPARTMENTS:
        flash("Unknown department.", "error")
        return redirect(url_for("team.departments"))
    stats = team_service.department_stats(department)
    member_workloads = [{"user": m, "workload": team_service.user_workload(m)} for m in stats["members"]]
    member_workloads.sort(key=lambda x: x["workload"]["pct"], reverse=True)
    return render_template("team/department_detail.html", stats=stats, member_workloads=member_workloads)


@team_bp.route("/smart-assign")
@login_required
@admin_required
def smart_assign():
    department = request.args.get("department") or None
    exclude = request.args.get("exclude_user_id", type=int)
    candidates = team_service.smart_assignment_candidates(department=department, exclude_user_id=exclude)
    return jsonify([
        {
            "id": c["user"].id, "name": c["user"].full_name, "email": c["user"].email,
            "department": c["user"].department, "availability": c["user"].availability,
            "workload_pct": c["workload"]["pct"], "workload_state": c["workload"]["state"],
            "sla_compliance": c["performance"]["sla_compliance"], "score": c["score"],
        }
        for c in candidates
    ])
