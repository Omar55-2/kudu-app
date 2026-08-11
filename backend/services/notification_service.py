"""
Everything related to telling people things happened: in-app
notifications, ticket history entries, and outgoing email.
"""
import smtplib
from email.mime.text import MIMEText

from extensions import db
from config import MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_SENDER
from models import Notification, TicketHistory, EmailLog, TicketView, Comment, Attachment, utcnow, to_aware


def log_history(ticket_id, user_id, action, old_value=None, new_value=None):
    db.session.add(TicketHistory(
        ticket_id=ticket_id, user_id=user_id, action=action,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
    ))


def notify(user_id, ticket_id, title, message):
    db.session.add(Notification(user_id=user_id, ticket_id=ticket_id, title=title, message=message))


def mark_ticket_viewed(ticket_id, user_id):
    view = TicketView.query.filter_by(ticket_id=ticket_id, user_id=user_id).first()
    if view:
        view.last_viewed_at = utcnow()
    else:
        db.session.add(TicketView(ticket_id=ticket_id, user_id=user_id, last_viewed_at=utcnow()))


def ticket_has_updates(ticket, user):
    """True if there's ticket activity (comment/attachment/update) the
    given user hasn't seen yet."""
    view = TicketView.query.filter_by(ticket_id=ticket.id, user_id=user.id).first()
    last_seen = to_aware(view.last_viewed_at) if view else to_aware(ticket.created_at)

    latest_comment = Comment.query.filter_by(ticket_id=ticket.id).order_by(Comment.created_at.desc()).first()
    latest_attachment = Attachment.query.filter_by(ticket_id=ticket.id).order_by(Attachment.created_at.desc()).first()

    candidates = [to_aware(ticket.updated_at)]
    if latest_comment:
        candidates.append(to_aware(latest_comment.created_at))
    if latest_attachment:
        candidates.append(to_aware(latest_attachment.created_at))
    return max(candidates) > last_seen


def get_ticket_stakeholders(ticket):
    """Everyone who should be notified about activity on this ticket."""
    from models import User  # local import to avoid a circular import
    ids = set()
    if ticket.created_by_id:
        ids.add(ticket.created_by_id)
    if ticket.assigned_to_id:
        ids.add(ticket.assigned_to_id)
    elif ticket.assigned_department:
        dept_users = User.query.filter_by(department=ticket.assigned_department, is_active=True).all()
        ids.update(u.id for u in dept_users)
    return ids


def render_ticket_email(ticket, heading, message):
    """A simple, real-looking HTML email for ticket notifications."""
    priority_colors = {
        "low": "#6b7280", "medium": "#2563eb", "high": "#d97706",
        "urgent": "#ea580c", "critical": "#dc2626",
    }
    color = priority_colors.get(ticket.priority, "#2563eb")
    due_str = ticket.due_date.strftime("%b %d, %Y %I:%M %p UTC") if ticket.due_date else "N/A"
    return f"""
    <div style="font-family: Arial, sans-serif; max-width:560px; margin:0 auto;
                border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;">
      <div style="background:#111827; color:#fff; padding:16px 20px;">
        <h2 style="margin:0; font-size:16px;">{heading}</h2>
      </div>
      <div style="padding:20px;">
        <p style="margin:0 0 14px; font-size:14px; color:#374151;">{message}</p>
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
          <tr><td style="padding:6px 0; color:#6b7280; width:110px;">Ticket #</td>
              <td style="padding:6px 0; font-weight:bold;">{ticket.ticket_number}</td></tr>
          <tr><td style="padding:6px 0; color:#6b7280;">Title</td>
              <td style="padding:6px 0;">{ticket.title}</td></tr>
          <tr><td style="padding:6px 0; color:#6b7280;">Priority</td>
              <td style="padding:6px 0;"><span style="color:{color}; font-weight:bold;
                  text-transform:capitalize;">{ticket.priority}</span></td></tr>
          <tr><td style="padding:6px 0; color:#6b7280;">Category</td>
              <td style="padding:6px 0; text-transform:capitalize;">{ticket.category}</td></tr>
          <tr><td style="padding:6px 0; color:#6b7280;">Status</td>
              <td style="padding:6px 0; text-transform:capitalize;">{ticket.status.replace('_', ' ')}</td></tr>
          <tr><td style="padding:6px 0; color:#6b7280;">SLA Due</td>
              <td style="padding:6px 0;">{due_str}</td></tr>
        </table>
        <div style="margin-top:14px; padding:12px; background:#f9fafb;
                    border-radius:6px; font-size:13px; color:#374151;">
          {ticket.description}
        </div>
      </div>
      <div style="padding:14px 20px; background:#f9fafb; font-size:11px; color:#9ca3af;">
        This is an automated notification from the Employee Ticketing System.
      </div>
    </div>
    """


def send_email(recipient, subject, html_body):
    """Send an email via Mailtrap SMTP and log the attempt. Never raises."""
    log = EmailLog(recipient=recipient, subject=subject, status="pending")
    db.session.add(log)
    db.session.flush()
    try:
        msg = MIMEText(html_body, "html")
        msg["Subject"] = subject
        msg["From"] = MAIL_SENDER
        msg["To"] = recipient
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_SENDER, [recipient], msg.as_string())
        log.status = "sent"
        log.sent_at = utcnow()
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
