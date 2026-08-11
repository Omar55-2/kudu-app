"""
Ticket lifecycle business logic - the "tickiting" queue/claim model kept
intact: a ticket is assigned to exactly one person OR one department
queue, never both. Routes call these functions and turn ValueError /
PermissionError / LookupError into flash messages or redirects.
"""
from datetime import timedelta

import backend.config as config
from backend.extensions import db
from backend.models import Ticket, TicketHistory, Notification, User, utcnow, to_aware
from backend.services.email_service import send_ticket_notification
from backend.services import delegation_service


def generate_ticket_number():
    last = Ticket.query.order_by(Ticket.id.desc()).first()
    next_id = (last.id + 1) if last else 1
    return Ticket.generate_number(next_id)


def log_history(ticket_id, user_id, action, old_value=None, new_value=None):
    db.session.add(TicketHistory(
        ticket_id=ticket_id, user_id=user_id, action=action,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
    ))


def notify(user_id, ticket_id, title, message):
    db.session.add(Notification(user_id=user_id, ticket_id=ticket_id, title=title, message=message))


def get_ticket_stakeholders(ticket):
    """Everyone who should hear about new activity on this ticket -
    including anyone currently covering the creator or assignee via an
    active delegation."""
    ids = set()
    if ticket.created_by_id:
        ids.add(ticket.created_by_id)
        ids.update(delegation_service.active_delegatee_ids_for(ticket.created_by_id))
    if ticket.assigned_to_id:
        ids.add(ticket.assigned_to_id)
        ids.update(delegation_service.active_delegatee_ids_for(ticket.assigned_to_id))
    elif ticket.assigned_department:
        dept_users = User.query.filter_by(department=ticket.assigned_department, is_active=True).all()
        ids.update(u.id for u in dept_users)
    return ids


def can_view_ticket(user, ticket):
    if user.is_admin:
        return True
    if ticket.created_by_id == user.id or ticket.assigned_to_id == user.id:
        return True
    # Unclaimed department ticket, visible to that department's members.
    if ticket.assigned_to_id is None and ticket.assigned_department == user.department:
        return True
    # Active delegatee: full view access to the delegator's tickets.
    covered = delegation_service.delegators_covered_by(user.id)
    if ticket.created_by_id in covered or ticket.assigned_to_id in covered:
        return True
    return False


def can_change_status(user, ticket, new_status):
    covered = delegation_service.delegators_covered_by(user.id)
    if new_status == "cancelled":
        return ticket.created_by_id == user.id or ticket.created_by_id in covered
    return ticket.assigned_to_id == user.id or ticket.assigned_to_id in covered


def maybe_auto_open(ticket, viewer):
    """The moment the assignee opens/claims a brand-new ticket, it moves
    itself from 'new' to 'open' automatically."""
    if viewer and ticket.status == "new" and ticket.assigned_to_id == viewer.id:
        old = ticket.status
        ticket.status = "open"
        log_history(ticket.id, viewer.id, "auto_status_change", old, "open")
        return True
    return False


def maybe_auto_close(ticket):
    if ticket.status == "resolved" and ticket.resolved_at:
        resolved_at = to_aware(ticket.resolved_at)
        if utcnow() - resolved_at >= config.AUTO_CLOSE_AFTER:
            ticket.status = "closed"
            ticket.closed_at = resolved_at + config.AUTO_CLOSE_AFTER
            log_history(ticket.id, None, "auto_closed", "resolved", "closed")
            return True
    return False


def ticket_sort_key(t):
    from datetime import datetime, timezone
    due = to_aware(t.due_date) or datetime.max.replace(tzinfo=timezone.utc)
    created = to_aware(t.created_at) or datetime.min.replace(tzinfo=timezone.utc)
    return (
        config.STATUS_RANK.get(t.status, 9),
        config.PRIORITY_RANK.get(t.priority, 9),
        due,
        -created.timestamp(),
    )


def create_ticket(user, title, description, category, priority, assigned_to_id, assigned_department):
    """Returns the created Ticket. Raises ValueError on bad input."""
    if bool(assigned_to_id) == bool(assigned_department):
        raise ValueError("Assign this ticket to exactly one person or one department.")

    assignee = None
    original_assignee = None
    if assigned_to_id:
        assignee = db.session.get(User, assigned_to_id)
        if not assignee or not assignee.is_active:
            raise ValueError("The selected assignee is not an active employee.")
        if assignee.availability in ("on_leave", "offline"):
            raise ValueError("That employee is on leave or offline and cannot be assigned tickets.")
 
        original_assignee = assignee
        effective_id = delegation_service.resolve_effective_assignee_id(assignee.id)
        if effective_id != assignee.id:
            effective_user = db.session.get(User, effective_id)
            if effective_user and effective_user.is_active:
                assignee = effective_user
    elif assigned_department not in config.DEPARTMENTS:
        raise ValueError(f"Department must be one of {config.DEPARTMENTS}")

    ticket = Ticket(
        ticket_number=generate_ticket_number(),
        title=title, description=description, category=category, priority=priority,
        created_by_id=user.id,
        assigned_to_id=assignee.id if assignee else None,
        assigned_department=None if assignee else assigned_department,
        due_date=utcnow() + timedelta(minutes=config.SLA_MINUTES[priority]),
    )
    db.session.add(ticket)
    db.session.flush()
    log_history(ticket.id, user.id, "created", None, ticket.status)

    if assignee:
        log_history(ticket.id, user.id, "assigned", None, assignee.full_name)
        if original_assignee and assignee.id != original_assignee.id:
            log_history(
                ticket.id, None, "auto_routed_delegation",
                original_assignee.full_name, assignee.full_name,
            )
        notify(assignee.id, ticket.id, "New ticket assigned",
               f"Ticket {ticket.ticket_number} was assigned to you by {user.full_name}.")
        send_ticket_notification(assignee.email, ticket, "New Ticket Assigned",
                                  f"A new ticket has been assigned to you by {user.full_name}.")
    else:
        log_history(ticket.id, user.id, "assigned_department", None, assigned_department)
        for member in User.query.filter_by(department=assigned_department, is_active=True).all():
            if member.id == user.id:
                continue
            notify(member.id, ticket.id, "New department ticket",
                   f"Ticket {ticket.ticket_number} was sent to the {assigned_department} queue.")
            send_ticket_notification(member.email, ticket, f"New {assigned_department} Ticket",
                                      f"A new ticket was submitted to {assigned_department} by "
                                      f"{user.full_name}. Anyone in the department can claim it.")

    db.session.commit()
    return ticket


def update_ticket(user, ticket, data):
    """data is a plain dict of fields to change, already permission-checked
    at the route layer for who's allowed to submit which fields."""
    if "status" in data and data["status"] != ticket.status:
        new_status = data["status"]
        if not user.is_admin:
            if not can_change_status(user, ticket, new_status):
                raise PermissionError(
                    "Only the assignee can update status (or the creator can cancel their own ticket)."
                )
            if new_status not in config.VALID_TRANSITIONS.get(ticket.status, set()):
                raise ValueError(f"Cannot move this ticket from '{ticket.status}' to '{new_status}'.")

        old_status = ticket.status
        ticket.status = new_status

        if new_status == "resolved":
            ticket.resolved_at = utcnow()
            due = to_aware(ticket.due_date)
            ticket.sla_met = due is None or ticket.resolved_at <= due
            if ticket.created_by:
                notify(ticket.created_by_id, ticket.id, "Ticket resolved",
                       f"Ticket {ticket.ticket_number} was resolved.")
                send_ticket_notification(ticket.created_by.email, ticket, "Your Ticket Was Resolved",
                                          "Your ticket has been marked resolved. It will auto-close "
                                          "in 3 days if not reopened.")
        elif new_status == "closed":
            ticket.closed_at = utcnow()
            if ticket.created_by:
                send_ticket_notification(ticket.created_by.email, ticket, "Your Ticket Was Closed",
                                          "Your ticket has been closed.")
        elif new_status == "reopened":
            ticket.resolved_at = None
            ticket.closed_at = None
            ticket.sla_met = None
            if ticket.assigned_to:
                notify(ticket.assigned_to_id, ticket.id, "Ticket reopened",
                       f"Ticket {ticket.ticket_number} was reopened.")
                send_ticket_notification(ticket.assigned_to.email, ticket, "Ticket Reopened",
                                          "This ticket has been reopened and needs attention again.")

        log_history(ticket.id, user.id, "status_changed", old_status, new_status)
        if ticket.created_by_id != user.id:
            notify(ticket.created_by_id, ticket.id, "Status changed",
                   f"Ticket {ticket.ticket_number} status changed to {new_status.replace('_', ' ')}.")

    if "priority" in data and data["priority"] != ticket.priority:
        if not user.is_admin:
            raise PermissionError("Only an admin can change priority.")
        old_priority = ticket.priority
        ticket.priority = data["priority"]
        ticket.due_date = ticket.created_at + timedelta(minutes=config.SLA_MINUTES[ticket.priority])
        log_history(ticket.id, user.id, "priority_changed", old_priority, ticket.priority)

    if "assigned_to_id" in data or "assigned_department" in data:
        if not user.is_admin:
            raise PermissionError("Only an admin can reassign tickets.")
        new_assignee_id = data.get("assigned_to_id")
        new_department = data.get("assigned_department")
        if new_assignee_id and new_department:
            raise ValueError("Assign to either a person or a department, not both.")

        old_label = (ticket.assigned_to.full_name if ticket.assigned_to
                     else (f"{ticket.assigned_department} queue" if ticket.assigned_department else "Unassigned"))

        if new_assignee_id:
            assignee = db.session.get(User, new_assignee_id)
            if not assignee or not assignee.is_active:
                raise ValueError("assigned_to_id must reference an active employee.")

            original_assignee = assignee
            effective_id = delegation_service.resolve_effective_assignee_id(assignee.id)
            if effective_id != assignee.id:
                effective_user = db.session.get(User, effective_id)
                if effective_user and effective_user.is_active:
                    assignee = effective_user

            ticket.assigned_to_id = assignee.id
            ticket.assigned_department = None
            log_history(ticket.id, user.id, "reassigned", old_label, assignee.full_name)
            if original_assignee.id != assignee.id:
                log_history(
                    ticket.id, None, "auto_routed_delegation",
                    original_assignee.full_name, assignee.full_name,
                )
            notify(assignee.id, ticket.id, "Ticket assigned",
                   f"Ticket {ticket.ticket_number} was assigned to you.")
            send_ticket_notification(assignee.email, ticket, "Ticket Assigned To You",
                                      f"{user.full_name} assigned this ticket to you.")
        elif new_department:
            ticket.assigned_to_id = None
            ticket.assigned_department = new_department
            log_history(ticket.id, user.id, "reassigned_department", old_label, new_department)
            for member in User.query.filter_by(department=new_department, is_active=True).all():
                notify(member.id, ticket.id, "New department ticket",
                       f"Ticket {ticket.ticket_number} was sent to {new_department}.")

    if "title" in data:
        ticket.title = data["title"]
    if "description" in data:
        ticket.description = data["description"]

    ticket.updated_at = utcnow()
    db.session.commit()
    return ticket


def claim_ticket(user, ticket):
    """Claim an unclaimed department-queue ticket."""
    if ticket.assigned_to_id is not None:
        raise ValueError("This ticket is already assigned to someone.")
    if not ticket.assigned_department:
        raise ValueError("This ticket has no department queue to claim from.")
    if not user.is_admin and user.department != ticket.assigned_department:
        raise PermissionError("You can only claim tickets sent to your own department.")

    ticket.assigned_to_id = user.id
    log_history(ticket.id, user.id, "claimed", f"{ticket.assigned_department} queue", user.full_name)
    maybe_auto_open(ticket, user)
    db.session.commit()
    return ticket


def open_ticket_counts():
    """Maps user_id -> number of currently-open tickets assigned to them.
    Used to power the 'assign to least busy person' picker."""
    from sqlalchemy import func
    rows = (
        db.session.query(Ticket.assigned_to_id, func.count(Ticket.id))
        .filter(
            Ticket.assigned_to_id.isnot(None),
            Ticket.status.notin_(["resolved", "closed", "cancelled"]),
        )
        .group_by(Ticket.assigned_to_id)
        .all()
    )
    return dict(rows)


# def employees_for_picker(employees):
#     """Serializable [{id, name, email, department, open_count}, ...] for the
#     front-end search + least-busy assignee picker."""
#     counts = open_ticket_counts()
#     return [
#         {
#             "id": e.id,
#             "name": e.full_name,
#             "email": e.email,
#             "department": e.department,
#             "open_count": counts.get(e.id, 0),
#         }
#         for e in employees
#     ]
def employees_for_picker(employees):
    """Serializable [{id, name, email, department, open_count}, ...] for the
    front-end search + least-busy assignee picker. Excludes anyone who is
    currently unavailable: has delegated their own work away, or is on
    leave/offline - they shouldn't be assignable to new tickets."""
    from backend.services import delegation_service
    counts = open_ticket_counts()
    result = []
    for e in employees:
        if delegation_service.active_delegation_for(e.id):
            continue  # currently delegating their own tickets away
        if e.availability in ("on_leave", "offline"):
            continue
        result.append({
            "id": e.id,
            "name": e.full_name,
            "email": e.email,
            "department": e.department,
            "open_count": counts.get(e.id, 0),
        })
    return result

def can_update_status(user, ticket):
    """Whether this user is even allowed to act on the status dropdown at
    all (separate from whether a *specific* transition is valid)."""
    if user.is_admin:
        return True
    if ticket.assigned_to_id == user.id:
        return True
    if ticket.created_by_id == user.id and "cancelled" in config.VALID_TRANSITIONS.get(ticket.status, set()):
        return True
    covered = delegation_service.delegators_covered_by(user.id)
    if ticket.assigned_to_id in covered:
        return True
    if ticket.created_by_id in covered and "cancelled" in config.VALID_TRANSITIONS.get(ticket.status, set()):
        return True
    return False


def delegation_owner_name(ticket, covered_ids):
    """Name of the delegator whose ticket this is, for the 'Delegated to
    Me' view. covered_ids is the collection of delegator IDs the viewer
    currently covers."""
    covered_ids = set(covered_ids)
    if ticket.assigned_to_id in covered_ids:
        return ticket.assigned_to.full_name
    if ticket.created_by_id in covered_ids:
        return ticket.created_by.full_name
    return None


def ticket_stats(tickets):
    """Computes summary stats from an already-fetched list of tickets, so
    the same helper works for 'my tickets', a department queue, or the
    whole system without building a second query."""
    now = utcnow()
    open_statuses = {"new", "open", "in_progress", "waiting", "on_hold", "reopened"}
    by_status = {}
    by_priority = {}
    open_count = 0
    breached = 0
    for t in tickets:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
        if t.status in open_statuses:
            open_count += 1
        if t.status not in ("resolved", "closed", "cancelled") and t.due_date and to_aware(t.due_date) < now:
            breached += 1
    return {
        "total": len(tickets), "open": open_count, "breached": breached,
        "by_status": by_status, "by_priority": by_priority,
    } 