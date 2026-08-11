"""
Connections - simple direct-message inbox between employees. Not
tied to tickets or departments; anyone active can message anyone else
active.
"""
from sqlalchemy import or_, and_

from backend.extensions import db
from backend.models import Message, User


def inbox_threads(user):
    """One row per conversation partner: the other user, the most
    recent message, and how many unread messages are waiting for
    `user` in that thread. Sorted by most recent activity."""
    msgs = (
        Message.query.filter(
            or_(Message.sender_id == user.id, Message.recipient_id == user.id)
        )
        .order_by(Message.created_at.desc())
        .all()
    )

    threads = {}
    for m in msgs:
        other_id = m.recipient_id if m.sender_id == user.id else m.sender_id
        if other_id not in threads:
            threads[other_id] = {"other_id": other_id, "last": m, "unread": 0}
        if m.recipient_id == user.id and not m.is_read:
            threads[other_id]["unread"] += 1

    ordered = sorted(threads.values(), key=lambda t: t["last"].created_at, reverse=True)
    for t in ordered:
        t["other"] = db.session.get(User, t["other_id"])
    return [t for t in ordered if t["other"] is not None]


def unread_count(user):
    return Message.query.filter_by(recipient_id=user.id, is_read=False).count()


def thread_with(user, other_user):
    return (
        Message.query.filter(
            or_(
                and_(Message.sender_id == user.id, Message.recipient_id == other_user.id),
                and_(Message.sender_id == other_user.id, Message.recipient_id == user.id),
            )
        )
        .order_by(Message.created_at.asc())
        .all()
    )


def mark_thread_read(user, other_user):
    Message.query.filter_by(
        sender_id=other_user.id, recipient_id=user.id, is_read=False
    ).update({"is_read": True}, synchronize_session=False)
    db.session.commit()


def send_message(sender, recipient_id, body):
    body = (body or "").strip()
    if not body:
        raise ValueError("Message cannot be empty.")
    recipient = db.session.get(User, recipient_id)
    if not recipient or not recipient.is_active:
        raise ValueError("That colleague is not available to message.")
    if recipient.id == sender.id:
        raise ValueError("You cannot message yourself.")
    msg = Message(sender_id=sender.id, recipient_id=recipient.id, body=body)
    db.session.add(msg)
    db.session.commit()
    return msg
