"""
Everything related to sending mail: verification links and ticket
notification emails. send_email() never raises - a failed email should
never break a request; it just gets logged to EmailLog for debugging.
"""
import secrets
import smtplib
from email.mime.text import MIMEText

from itsdangerous import URLSafeTimedSerializer

import backend.config as config
from backend.extensions import db
from backend.models import EmailLog, EmailVerification, User, utcnow


def create_verification_token(user):
    token = secrets.token_urlsafe(32)
    db.session.add(EmailVerification(
        user_id=user.id,
        token=token,
        expires_at=utcnow() + config.EMAIL_VERIFY_EXPIRES,
    ))
    return token


def send_email(recipient, subject, html_body):
    log = EmailLog(recipient=recipient, subject=subject, status="pending")
    db.session.add(log)
    db.session.flush()
    try:
        msg = MIMEText(html_body, "html")
        msg["Subject"] = subject
        msg["From"] = config.MAIL_SENDER
        msg["To"] = recipient
        with smtplib.SMTP(config.MAIL_SERVER, config.MAIL_PORT, timeout=10) as server:
            if config.MAIL_USE_TLS:
                server.starttls()
            if config.MAIL_USERNAME:
                server.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
            server.sendmail(config.MAIL_SENDER, [recipient], msg.as_string())
        log.status = "sent"
        log.sent_at = utcnow()
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
    return log.status == "sent"


def _email_shell(heading, body_html):
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;
                border:1px solid #e7e2da;border-radius:12px;overflow:hidden;">
      <div style="background:#1c1917;color:#fff;padding:20px 24px;">
        <h2 style="margin:0;font-size:18px;">
          <span style="color:#d97706;">Kudu</span> Ticketing
        </h2>
      </div>
      <div style="padding:24px;color:#292524;">
        <h3 style="margin:0 0 14px;font-size:16px;">{heading}</h3>
        {body_html}
      </div>
      <div style="padding:14px 24px;background:#f7f5f2;font-size:11px;color:#9a938a;">
        This is an automated message from the Kudu Ticketing platform.
      </div>
    </div>
    """


def send_verification_email(user, verify_url):
    body = f"""
      <p style="margin:0 0 16px;color:#44403c;">Hi {user.full_name},</p>
      <p style="margin:0 0 20px;color:#57534e;line-height:1.6;">
        Thanks for creating a Kudu Ticketing account. Confirm your email to
        activate your account and start submitting and tracking tickets.
      </p>
      <a href="{verify_url}" style="display:inline-block;background:#b45309;color:#fff;
         padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">
        Verify my email
      </a>
      <p style="margin:20px 0 0;font-size:12px;color:#9a938a;">
        This link expires in 24 hours. If you didn't create this account, you
        can ignore this email.
      </p>
    """
    return send_email(user.email, "Verify your Kudu Ticketing account", _email_shell(
        "Verify your account", body,
    ))


def create_password_reset_token(user):
    serializer = URLSafeTimedSerializer(config.SECRET_KEY)

    return serializer.dumps({
        "user_id": user.id,
        "password_hash": user.password_hash,
    })


def verify_password_reset_token(token):
    serializer = URLSafeTimedSerializer(config.SECRET_KEY)

    try:
        data = serializer.loads(
            token,
            max_age=int(config.EMAIL_VERIFY_EXPIRES.total_seconds())
        )
    except Exception:
        return None

    user_id = data.get("user_id")
    password_hash = data.get("password_hash")

    if not user_id or not password_hash:
        return None

    user = db.session.get(User, user_id)

    if not user:
        return None

    # Makes old reset links invalid after the password changes.
    if user.password_hash != password_hash:
        return None

    return user


def send_password_reset_email(user, reset_url):
    body = f"""
      <p style="margin:0 0 16px;color:#44403c;">
        Hi {user.full_name},
      </p>

      <p style="margin:0 0 20px;color:#57534e;line-height:1.6;">
        We received a request to reset your Kudu Ticketing password.
        Click the button below to choose a new password.
      </p>

      <a href="{reset_url}"
         style="display:inline-block;background:#b45309;color:#fff;
         padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">
        Reset my password
      </a>

      <p style="margin:20px 0 0;font-size:12px;color:#9a938a;">
        This link expires in 24 hours. If you did not request a password reset,
        you can safely ignore this email.
      </p>
    """

    return send_email(
        user.email,
        "Reset your Kudu Ticketing password",
        _email_shell("Reset your password", body),
    )


def send_ticket_notification(recipient, ticket, heading, message):
    priority_colors = {
        "low": "#78716c", "medium": "#2563eb", "high": "#b45309",
        "urgent": "#c2410c", "critical": "#dc2626",
    }
    color = priority_colors.get(ticket.priority, "#b45309")
    due_str = ticket.due_date.strftime("%b %d, %Y %H:%M UTC") if ticket.due_date else "N/A"
    body = f"""
      <p style="margin:0 0 16px;color:#44403c;">{message}</p>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr><td style="padding:6px 0;color:#78716c;width:120px;">Ticket</td>
            <td style="padding:6px 0;font-weight:700;">{ticket.ticket_number}</td></tr>
        <tr><td style="padding:6px 0;color:#78716c;">Title</td>
            <td style="padding:6px 0;">{ticket.title}</td></tr>
        <tr><td style="padding:6px 0;color:#78716c;">Priority</td>
            <td style="padding:6px 0;color:{color};font-weight:700;text-transform:capitalize;">
              {ticket.priority}</td></tr>
        <tr><td style="padding:6px 0;color:#78716c;">Status</td>
            <td style="padding:6px 0;text-transform:capitalize;">
              {ticket.status.replace('_', ' ')}</td></tr>
        <tr><td style="padding:6px 0;color:#78716c;">Due By</td>
            <td style="padding:6px 0;">{due_str}</td></tr>
      </table>
    """
    return send_email(recipient, f"[{ticket.ticket_number}] {heading}", _email_shell(heading, body))

