"""
Time-bound user delegation ("out-of-office coverage").

Rules enforced here:
- A user may have at most one pending/active outgoing delegation at a time.
- No self-delegation.
- A delegatee must not currently have their own active/pending outgoing
  delegation - they're "away" and unavailable to receive new coverage.
- A delegator must not currently be someone else's active/pending
  delegatee - they're "away" and unavailable to delegate their own work.
  (Together these two rules mean delegation chains can never form.)
- end_time must be in the future and after start_time.
"""
from backend.models import User, UserDelegation, Notification, to_aware, utcnow
from backend.extensions import db


# --- Time-aware lookups (routing / access / notification fanout) ----------

def active_delegation_for(delegator_id):
    """The currently time-active delegation where delegator_id is the
    delegator, or None."""
    candidates = UserDelegation.query.filter_by(delegator_id=delegator_id, is_active=True).all()
    for d in candidates:
        if d.is_currently_active():
            return d
    return None


def active_delegations_received_by(delegatee_id):
    """All currently time-active delegations where delegatee_id is the delegatee."""
    candidates = UserDelegation.query.filter_by(delegatee_id=delegatee_id, is_active=True).all()
    return [d for d in candidates if d.is_currently_active()]

 
def delegators_covered_by(delegatee_id, max_depth=2):
    """User IDs currently delegating their work to delegatee_id, right now,
    including transitively up the chain (if b covers a and c covers b, c
    also effectively covers a's tickets)."""
    covered = set()
    frontier = {delegatee_id}
    for _ in range(max_depth):
        next_frontier = set()
        for uid in frontier:
            candidates = UserDelegation.query.filter_by(delegatee_id=uid, is_active=True).all()
            for d in candidates:
                if d.is_currently_active() and d.delegator_id not in covered:
                    covered.add(d.delegator_id)
                    next_frontier.add(d.delegator_id)
        if not next_frontier:
            break
        frontier = next_frontier
    return list(covered)

def active_delegatee_ids_for(delegator_id):
    """Delegatee ID covering delegator_id right now, if any, for notification
    fanout."""
    d = active_delegation_for(delegator_id)
    return [d.delegatee_id] if d else []


def resolve_effective_assignee_id(user_id, max_depth=3):
    """Returns the user ID a ticket should actually land on right now.
    Delegation chains can't be created anymore (see module docstring), so
    this only ever resolves one hop, but stays loop-based for safety."""
    if user_id is None:
        return None
    visited = {user_id}
    current_id = user_id
    for _ in range(max_depth):
        delegation = active_delegation_for(current_id)
        if not delegation:
            break
        next_id = delegation.delegatee_id
        if next_id in visited:
            break
        visited.add(next_id)
        current_id = next_id
    return current_id


# --- Structural lookups (ignore timing; used only for validation) ---------
# A pending delegation still "reserves" its people even before it starts.

def _structural_outgoing_delegation_for(user_id, exclude_id=None):
    q = UserDelegation.query.filter_by(delegator_id=user_id, is_active=True)
    if exclude_id:
        q = q.filter(UserDelegation.id != exclude_id)
    return q.first()


# def _structural_incoming_delegation_for(user_id, exclude_id=None):
#     q = UserDelegation.query.filter_by(delegatee_id=user_id, is_active=True)
#     if exclude_id:
#         q = q.filter(UserDelegation.id != exclude_id)
#     return q.first()
MAX_DELEGATION_DEPTH = 3

def _delegation_chain_depth(user_id):
    """How many hops deep this user already sits in an active/pending
    delegation chain (0 if they're not currently anyone's delegatee)."""
    depth = 0
    current_id = user_id
    visited = {user_id}
    while True:
        incoming = UserDelegation.query.filter_by(delegatee_id=current_id, is_active=True).first()
        if not incoming:
            break
        depth += 1
        current_id = incoming.delegator_id
        if current_id in visited or depth >= MAX_DELEGATION_DEPTH + 1:
            break
        visited.add(current_id)
    return depth


def validate_new_delegation(delegator, delegatee_id, start_time, end_time, exclude_delegation_id=None):
    if not delegatee_id:
        raise ValueError("Please select a colleague to delegate to.")
    delegatee_id = int(delegatee_id)
    if delegatee_id == delegator.id:
        raise ValueError("You cannot delegate to yourself.")

    delegatee = db.session.get(User, delegatee_id)
    if not delegatee or not delegatee.is_active:
        raise ValueError("The selected colleague is not an active employee.")
    if delegatee.availability in ("on_leave", "offline"):
        raise ValueError("That colleague is on leave or offline and cannot receive delegations.")

    if not start_time or not end_time:
        raise ValueError("Start and end date/time are required.")
    if end_time <= utcnow():
        raise ValueError("End date/time must be in the future.")
    if end_time <= start_time:
        raise ValueError("End date/time must be after the start date/time.")

    if _structural_outgoing_delegation_for(delegator.id, exclude_delegation_id):
        raise ValueError(
            "You already have an active or pending delegation. Edit or revoke it before creating a new one."
        )
    if _structural_outgoing_delegation_for(delegatee_id, exclude_delegation_id):
        raise ValueError(
            "That colleague already has their own delegation set up right now and is not "
            "available to receive delegations. Try again once it ends."
        )

    # Chain-depth check replaces the old "you're someone's delegatee, full
    # stop" rule: a->b->c is allowed, c->d is not (chain would exceed 3).
    depth = _delegation_chain_depth(delegator.id)
    if depth + 1 > MAX_DELEGATION_DEPTH:
        raise ValueError(
            f"This delegation chain has already reached the maximum depth of "
            f"{MAX_DELEGATION_DEPTH} (a→b→c). It cannot be extended further until an "
            f"earlier link in the chain ends."
        )

    return delegatee

# --- Validation / mutation -------------------------------------------

# def validate_new_delegation(delegator, delegatee_id, start_time, end_time, exclude_delegation_id=None):
#     """Raises ValueError on invalid input. Returns the delegatee User.
#     exclude_delegation_id lets an in-progress edit ignore its own row when
#     checking "already has a delegation"."""
#     if not delegatee_id:
#         raise ValueError("Please select a colleague to delegate to.")
#     delegatee_id = int(delegatee_id)
#     if delegatee_id == delegator.id:
#         raise ValueError("You cannot delegate to yourself.")

#     delegatee = db.session.get(User, delegatee_id)
#     if not delegatee or not delegatee.is_active:
#         raise ValueError("The selected colleague is not an active employee.")

#     if not start_time or not end_time:
#         raise ValueError("Start and end date/time are required.")
#     if end_time <= utcnow():
#         raise ValueError("End date/time must be in the future.")
#     if end_time <= start_time:
#         raise ValueError("End date/time must be after the start date/time.")

#     if _structural_outgoing_delegation_for(delegator.id, exclude_delegation_id):
#         raise ValueError(
#             "You already have an active or pending delegation. Edit or revoke it before creating a new one."
#         )
#     if _structural_incoming_delegation_for(delegator.id, exclude_delegation_id):
#         raise ValueError(
#             "You are currently covering someone else's tickets as a delegatee and cannot "
#             "delegate your own tickets until that coverage ends."
#         )
#     if _structural_outgoing_delegation_for(delegatee_id, exclude_delegation_id):
#         raise ValueError(
#             "That colleague already has their own delegation set up right now and is not "
#             "available to receive delegations. Try again once it ends."
#         )

#     return delegatee


def create_delegation(delegator, delegatee_id, start_time, end_time):
    delegatee = validate_new_delegation(delegator, delegatee_id, start_time, end_time)
    delegation = UserDelegation(
        delegator_id=delegator.id,
        delegatee_id=delegatee.id,
        start_time=start_time,
        end_time=end_time,
    )
    db.session.add(delegation)
    db.session.commit()
    _notify_delegation_event(delegation, "created")
    db.session.commit()
    return delegation


def update_delegation(user, delegation_id, delegatee_id, start_time, end_time):
    """Edit the delegatee and/or time window of a pending or active
    delegation. Only the original delegator or an admin may do this."""
    delegation = db.session.get(UserDelegation, delegation_id)
    if not delegation:
        raise ValueError("Delegation not found.")
    if delegation.delegator_id != user.id and not user.is_admin:
        raise PermissionError("You can only edit your own delegations.")
    if delegation.status_label() not in ("pending", "active"):
        raise ValueError("Only a pending or active delegation can be edited.")

    old_delegatee = delegation.delegatee
    old_start = to_aware(delegation.start_time)
    old_end = to_aware(delegation.end_time)

    new_delegatee = validate_new_delegation(
        delegation.delegator, delegatee_id, start_time, end_time,
        exclude_delegation_id=delegation.id,
    )

    delegatee_changed = new_delegatee.id != old_delegatee.id
    times_changed = old_start != start_time or old_end != end_time

    delegation.delegatee_id = new_delegatee.id
    delegation.start_time = start_time
    delegation.end_time = end_time
    if times_changed:
        # Re-arm the start/expiry notifications for the new window.
        delegation.start_notified = False
        delegation.end_notified = False

    db.session.commit()

    if delegatee_changed:
        _notify_delegatee_changed(delegation, old_delegatee, new_delegatee)
    elif times_changed:
        _notify_delegation_event(delegation, "updated")
    db.session.commit()
    return delegation


def revoke_delegation(user, delegation_id):
    """Only the delegator or an admin may revoke. Raises on bad input."""
    delegation = db.session.get(UserDelegation, delegation_id)
    if not delegation:
        raise ValueError("Delegation not found.")
    if delegation.delegator_id != user.id and not user.is_admin:
        raise PermissionError("You can only revoke your own delegations.")
    if delegation.revoked_at is not None or not delegation.is_active:
        raise ValueError("This delegation is already inactive.")

    delegation.is_active = False
    delegation.revoked_at = utcnow()
    delegation.end_notified = True
    db.session.commit()
    _notify_delegation_event(delegation, "revoked")
    db.session.commit()
    return delegation


def sync_delegations():
    """Lazily transitions delegations between pending/active/expired and
    fires notifications exactly once per transition. Called near the top
    of every authenticated request (no cron in this deployment)."""
    now = utcnow()
    live = UserDelegation.query.filter_by(is_active=True).all()
    changed = False
    for d in live:
        start = to_aware(d.start_time)
        end = to_aware(d.end_time)

        if not d.start_notified and now >= start:
            _notify_delegation_event(d, "started")
            d.start_notified = True
            changed = True

        if now > end:
            if not d.end_notified:
                _notify_delegation_event(d, "expired")
                d.end_notified = True
            d.is_active = False
            changed = True

    if changed:
        db.session.commit()


def _notify_delegation_event(delegation, event):
    delegator = delegation.delegator
    delegatee = delegation.delegatee
    start_str = to_aware(delegation.start_time).strftime('%Y-%m-%d %H:%M')
    end_str = to_aware(delegation.end_time).strftime('%Y-%m-%d %H:%M')
    window = f"{start_str} to {end_str} UTC"

    messages = {
        "created": (
            "Delegation scheduled",
            f"You scheduled a delegation to {delegatee.full_name} ({window}).",
            f"{delegator.full_name} has scheduled a delegation of their tickets to you ({window}).",
            f"{delegator.full_name} scheduled a delegation to {delegatee.full_name} ({window}).",
        ),
        "started": (
            "Delegation now active",
            f"Your delegation to {delegatee.full_name} is now active until {end_str} UTC.",
            f"You now have full access to {delegator.full_name}'s tickets until {end_str} UTC.",
            f"Delegation from {delegator.full_name} to {delegatee.full_name} is now active.",
        ),
        "expired": (
            "Delegation ended",
            f"Your delegation to {delegatee.full_name} has ended.",
            f"Your access to {delegator.full_name}'s tickets has ended.",
            f"Delegation from {delegator.full_name} to {delegatee.full_name} has expired.",
        ),
        "revoked": (
            "Delegation revoked",
            f"Your delegation to {delegatee.full_name} was revoked.",
            f"{delegator.full_name} revoked your access to their tickets.",
            f"Delegation from {delegator.full_name} to {delegatee.full_name} was revoked.",
        ),
        "updated": (
            "Delegation updated",
            f"You updated your delegation to {delegatee.full_name} ({window}).",
            f"{delegator.full_name} updated their delegation to you - now {window}.",
            f"{delegator.full_name} updated their delegation to {delegatee.full_name} ({window}).",
        ),
    }
    title, delegator_msg, delegatee_msg, admin_msg = messages[event]

    db.session.add(Notification(user_id=delegator.id, ticket_id=None, title=title, message=delegator_msg))
    db.session.add(Notification(user_id=delegatee.id, ticket_id=None, title=title, message=delegatee_msg))

    admins = User.query.filter(
        User.role == "admin", User.is_active.is_(True), User.id.notin_([delegator.id, delegatee.id])
    ).all()
    for admin in admins:
        db.session.add(Notification(user_id=admin.id, ticket_id=None, title=title, message=admin_msg))


def _notify_delegatee_changed(delegation, old_delegatee, new_delegatee):
    delegator = delegation.delegator
    start_str = to_aware(delegation.start_time).strftime('%Y-%m-%d %H:%M')
    end_str = to_aware(delegation.end_time).strftime('%Y-%m-%d %H:%M')
    window = f"{start_str} to {end_str} UTC"

    db.session.add(Notification(
        user_id=old_delegatee.id, ticket_id=None, title="Delegation reassigned",
        message=f"{delegator.full_name}'s delegation was reassigned to {new_delegatee.full_name}. "
                f"You no longer cover their tickets.",
    ))
    db.session.add(Notification(
        user_id=new_delegatee.id, ticket_id=None, title="Delegation assigned to you",
        message=f"{delegator.full_name} has delegated their tickets to you ({window}).",
    ))
    db.session.add(Notification(
        user_id=delegator.id, ticket_id=None, title="Delegation updated",
        message=f"Your delegation is now assigned to {new_delegatee.full_name} ({window}).",
    ))
    admins = User.query.filter(
        User.role == "admin", User.is_active.is_(True),
        User.id.notin_([delegator.id, old_delegatee.id, new_delegatee.id]),
    ).all()
    for admin in admins:
        db.session.add(Notification(
            user_id=admin.id, ticket_id=None, title="Delegation updated",
            message=f"{delegator.full_name}'s delegation was reassigned from "
                    f"{old_delegatee.full_name} to {new_delegatee.full_name}.",
        ))