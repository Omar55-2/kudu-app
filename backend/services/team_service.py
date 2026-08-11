"""
Team management: workload scoring, SLA pressure, department stats, and
smart-assignment candidate ranking. This is read-only analysis built on
top of Ticket.sla_info(), delegation_service, and the existing ticket
assignment model - it never creates parallel data.
"""
import backend.config as config
from backend.models import Ticket, User, Comment, TicketHistory, to_aware
from backend.services import delegation_service


def sla_pressure_points(ticket):
    sla = ticket.sla_info()
    if not sla:
        return 0
    if sla["breached"]:
        return config.SLA_PRESSURE["breached"]
    due = to_aware(ticket.due_date)
    if due is None:
        return 0
    from backend.models import utcnow
    minutes_left = (due - utcnow()).total_seconds() / 60
    if 0 < minutes_left <= config.SLA_RISK_MINUTES:
        return config.SLA_PRESSURE["at_risk"]
    return config.SLA_PRESSURE["normal"]


def is_sla_at_risk(ticket):
    return sla_pressure_points(ticket) == config.SLA_PRESSURE["at_risk"]


def is_sla_breached(ticket):
    sla = ticket.sla_info()
    return bool(sla and sla["breached"])


def active_tickets_for(user_id):
    return Ticket.query.filter(
        Ticket.assigned_to_id == user_id,
        Ticket.status.in_(config.ACTIVE_TICKET_STATUSES),
    ).all()


def workload_points(tickets):
    points = 0
    for t in tickets:
        points += config.WORKLOAD_WEIGHTS.get(t.priority, 0)
        points += sla_pressure_points(t)
    return points


def workload_state(pct):
    for lo, hi, label in config.WORKLOAD_STATES:
        if lo <= pct <= hi:
            return label
    return config.WORKLOAD_STATES[-1][2]


def user_workload(user):
    tickets = active_tickets_for(user.id)
    points = workload_points(tickets)
    pct = min(round(points / config.WORKLOAD_CAPACITY * 100), 999)
    by_priority, at_risk, breached = {}, 0, 0
    for t in tickets:
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
        if is_sla_at_risk(t):
            at_risk += 1
        if is_sla_breached(t):
            breached += 1
    return {
        "points": points, "capacity": config.WORKLOAD_CAPACITY, "pct": pct,
        "state": workload_state(pct), "active_count": len(tickets),
        "by_priority": by_priority, "sla_at_risk": at_risk, "sla_breached": breached,
    }


def sla_performance(user):
    """SLA compliance, average resolution/first-response time, and reopen
    rate for tickets assigned to this user. Any metric that can't be
    honestly computed from what's tracked comes back as None rather than
    a made-up number."""
    resolved_like = Ticket.query.filter(
        Ticket.assigned_to_id == user.id, Ticket.status.in_(["resolved", "closed"])
    ).all()

    sla_known = [t for t in resolved_like if t.sla_met is not None]
    compliance = (
        round(sum(1 for t in sla_known if t.sla_met) / len(sla_known) * 100)
        if sla_known else None
    )

    resolved_with_time = [t for t in resolved_like if t.resolved_at]
    if resolved_with_time:
        total_seconds = sum(
            (to_aware(t.resolved_at) - to_aware(t.created_at)).total_seconds()
            for t in resolved_with_time
        )
        avg_resolution_hours = round(total_seconds / len(resolved_with_time) / 3600, 1)
    else:
        avg_resolution_hours = None

    # First response = when the assignee actually opened/acknowledged the
    # ticket (new -> open transition), not the first comment - many tickets
    # get worked without a comment ever being posted.
    all_assigned = Ticket.query.filter(Ticket.assigned_to_id == user.id).all()
    response_seconds = []
    for t in all_assigned:
        opened_event = (
            TicketHistory.query.filter(
                TicketHistory.ticket_id == t.id,
                TicketHistory.action == "auto_status_change",
                TicketHistory.new_value == "open",
            )
            .order_by(TicketHistory.created_at.asc())
            .first()
        )
        if opened_event:
            response_seconds.append(
                (to_aware(opened_event.created_at) - to_aware(t.created_at)).total_seconds()
            )
    avg_first_response_hours = (
        round(sum(response_seconds) / len(response_seconds) / 3600, 1) if response_seconds else None
    )

    reopened_count = TicketHistory.query.filter(
        TicketHistory.user_id == user.id, TicketHistory.action == "status_changed",
        TicketHistory.new_value == "reopened",
    ).count()
    ever_resolved = len(resolved_like) + reopened_count
    reopen_rate = round(reopened_count / ever_resolved * 100) if ever_resolved else None

    return {
        "sla_compliance": compliance,
        "avg_resolution_hours": avg_resolution_hours,
        "avg_first_response_hours": avg_first_response_hours,
        "reopened_count": reopened_count,
        "resolved_count": len(resolved_like),
        "reopen_rate": reopen_rate,
    }


def team_overview_stats():
    members = User.query.filter_by(is_active=True).all()
    available = sum(1 for m in members if m.availability == "available")
    active_tickets = Ticket.query.filter(Ticket.status.in_(config.ACTIVE_TICKET_STATUSES)).all()
    critical_tickets = sum(1 for t in active_tickets if t.priority == "critical")
    unassigned = sum(1 for t in active_tickets if t.assigned_to_id is None)
    sla_at_risk = sum(1 for t in active_tickets if is_sla_at_risk(t))
    return {
        "members": len(members), "available": available, "open_tickets": len(active_tickets),
        "critical_tickets": critical_tickets, "sla_at_risk": sla_at_risk, "unassigned": unassigned,
    }


def team_alerts():
    """Admin-only alert feed - every line is derived straight from the DB."""
    alerts = []
    active_tickets = Ticket.query.filter(Ticket.status.in_(config.ACTIVE_TICKET_STATUSES)).all()

    critical = [t for t in active_tickets if t.priority == "critical"]
    if critical:
        alerts.append({"level": "critical", "text": f"{len(critical)} critical ticket(s) need attention"})

    breached = [t for t in active_tickets if is_sla_breached(t)]
    if breached:
        alerts.append({"level": "critical", "text": f"{len(breached)} ticket(s) have breached SLA"})

    at_risk = [t for t in active_tickets if is_sla_at_risk(t)]
    if at_risk:
        alerts.append({"level": "warning", "text": f"{len(at_risk)} ticket(s) approaching SLA"})

    unassigned = [t for t in active_tickets if t.assigned_to_id is None]
    if unassigned:
        alerts.append({"level": "warning", "text": f"{len(unassigned)} ticket(s) unassigned"})

    members = User.query.filter_by(is_active=True).all()
    overloaded = [m for m in members if user_workload(m)["state"] == "Overloaded"]
    if overloaded:
        names = ", ".join(m.full_name for m in overloaded[:5])
        alerts.append({"level": "warning", "text": f"Overloaded: {names}"})

    covering = [m for m in members if delegation_service.active_delegation_for(m.id)]
    if covering:
        alerts.append({"level": "info", "text": f"{len(covering)} employee(s) currently delegating their work"})

    dept_over_capacity = []
    for d in config.DEPARTMENTS:
        stats = department_stats(d)
        if stats["member_count"] and stats["capacity_pct"] >= 90:
            dept_over_capacity.append(d)
    if dept_over_capacity:
        alerts.append({"level": "warning", "text": f"Departments approaching capacity: {', '.join(dept_over_capacity)}"})

    return alerts


def department_stats(dept):
    members = User.query.filter_by(department=dept, is_active=True).all()
    member_ids = [m.id for m in members]

    from backend.extensions import db
    if member_ids:
        dept_tickets = Ticket.query.filter(
            db.or_(Ticket.assigned_department == dept, Ticket.assigned_to_id.in_(member_ids))
        ).all()
    else:
        dept_tickets = Ticket.query.filter(Ticket.assigned_department == dept).all()

    active = [t for t in dept_tickets if t.status in config.ACTIVE_TICKET_STATUSES]
    unassigned = [t for t in active if t.assigned_to_id is None and t.assigned_department == dept]
    critical = [t for t in active if t.priority == "critical"]
    at_risk = [t for t in active if is_sla_at_risk(t)]
    resolved = [t for t in dept_tickets if t.status in ("resolved", "closed") and t.sla_met is not None]
    sla_pct = round(sum(1 for t in resolved if t.sla_met) / len(resolved) * 100) if resolved else None

    total_points = sum(user_workload(m)["points"] for m in members)
    total_capacity = len(members) * config.WORKLOAD_CAPACITY
    capacity_pct = round(total_points / total_capacity * 100) if total_capacity else 0

    status_counts = {}
    for t in active:
        status_counts[t.status] = status_counts.get(t.status, 0) + 1

    return {
        "department": dept, "members": members, "member_count": len(members),
        "open_tickets": len(active), "unassigned": unassigned, "critical": len(critical),
        "sla_at_risk": len(at_risk), "sla_compliance": sla_pct, "capacity_pct": capacity_pct,
        "status_counts": status_counts,
    }


def smart_assignment_candidates(department=None, exclude_user_id=None):
    """Ranked candidate employees for a ticket. Read-only - never assigns
    anything; the admin picks from the results."""
    query = User.query.filter_by(is_active=True)
    if department:
        query = query.filter(User.department == department)

    scored = []
    for u in query.all():
        if exclude_user_id and u.id == exclude_user_id:
            continue
        if u.availability in ("offline", "on_leave"):
            continue

        w = user_workload(u)
        perf = sla_performance(u)
        score = 100.0
        score -= w["pct"] * 0.6
        if u.availability == "dnd":
            score -= 25
        elif u.availability == "busy":
            score -= 10
        if perf["sla_compliance"] is not None:
            score += (perf["sla_compliance"] - 90) * 0.3
        score -= w["sla_at_risk"] * 3

        scored.append({"user": u, "workload": w, "performance": perf, "score": round(score, 1)})

    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:5]



def effective_availability(user):
    """The availability the person should actually be shown as. A stated
    availability of 'available'/'busy'/'dnd' is overridden by 'delegating'
    if they've handed off their own tickets right now - they aren't
    truly available even if their manual toggle still says so."""
    from backend.services import delegation_service
    if delegation_service.active_delegation_for(user.id):
        return "delegating"
    return user.availability