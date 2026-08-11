"""
Database models.

Ticket assignment rule (kept from the "tickiting" logic): a ticket is
assigned to EITHER one specific employee OR a whole department queue
(until someone claims it) - never both at once.

Auth flow (kept from the "ticketing_system" logic): new accounts must
verify their email via a signed link before they can log in - no OTP.
"""
from datetime import datetime, timezone

from backend.extensions import db, bcrypt
from backend.config import ROLES, DEPARTMENTS, STATUSES, PRIORITIES, CATEGORIES, AVAILABILITY_STATES

def utcnow():
    return datetime.now(timezone.utc) 


def to_aware(dt):
    """MariaDB can return naive datetimes; treat naive values as UTC so
    they can always be safely compared against utcnow()."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.Enum(*ROLES, name="role_enum"), nullable=False, default="employee")
    department = db.Column(
        db.Enum(*DEPARTMENTS, name="department_enum"), nullable=False, default="Support"
    )
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    availability = db.Column(
        db.Enum(*AVAILABILITY_STATES, name="availability_enum"), nullable=False, default="available"
    )
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    created_tickets = db.relationship(
        "Ticket", foreign_keys="Ticket.created_by_id", back_populates="created_by"
    )
    assigned_tickets = db.relationship(
        "Ticket", foreign_keys="Ticket.assigned_to_id", back_populates="assigned_to"
    )

    # Flask-Login required properties/methods
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def set_password(self, raw_password):
        self.password_hash = bcrypt.generate_password_hash(raw_password).decode("utf-8")

    def check_password(self, raw_password):
        return bcrypt.check_password_hash(self.password_hash, raw_password)

    @property
    def is_admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<User {self.email}>"


class EmailVerification(db.Model):
    __tablename__ = "email_verifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user = db.relationship("User")


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    ticket_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)

    status = db.Column(db.Enum(*STATUSES, name="status_enum"), default="new", nullable=False)
    priority = db.Column(db.Enum(*PRIORITIES, name="priority_enum"), default="medium", nullable=False)
    category = db.Column(db.Enum(*CATEGORIES, name="category_enum"), nullable=False)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # A ticket is assigned to EITHER one specific employee OR a whole
    # department queue (until someone claims it) - never both at once.
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    assigned_department = db.Column(
        db.Enum(*DEPARTMENTS, name="assigned_department_enum"), nullable=True
    )

    due_date = db.Column(db.DateTime(timezone=True), nullable=True)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sla_met = db.Column(db.Boolean, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    created_by = db.relationship("User", foreign_keys=[created_by_id], back_populates="created_tickets")
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id], back_populates="assigned_tickets")

    comments = db.relationship("Comment", back_populates="ticket", cascade="all, delete-orphan")
    history = db.relationship(
        "TicketHistory", back_populates="ticket", cascade="all, delete-orphan",
        order_by="TicketHistory.created_at",
    )
    attachments = db.relationship("Attachment", back_populates="ticket", cascade="all, delete-orphan")

    def sla_info(self):
        if self.status == "cancelled":
            return None
        if self.status in ("resolved", "closed") and self.sla_met is not None:
            return {
                "breached": not self.sla_met,
                "label": "SLA Met" if self.sla_met else "SLA Breached",
                "css": "sla-met" if self.sla_met else "sla-breach",
            }
        due = to_aware(self.due_date)
        if due is None:
            return None
        delta = (due - utcnow()).total_seconds()
        if delta <= 0:
            return {"breached": True, "label": "SLA Breached", "css": "sla-breach"}
        days = int(delta // 86400)
        hours = int((delta % 86400) // 3600)
        minutes = int((delta % 3600) // 60)
        if days > 0:
            label = f"{days}d {hours}h left"
        elif hours > 0:
            label = f"{hours}h {minutes}m left"
        else:
            label = f"{max(minutes, 1)}m left"
        return {"breached": False, "label": label, "css": "sla-ok"}

    @staticmethod
    def generate_number(next_id):
        return f"KUDU-{next_id:06d}"

    def __repr__(self):
        return f"<Ticket {self.ticket_number}>"


class TicketHistory(db.Model):
    __tablename__ = "ticket_history"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    old_value = db.Column(db.String(255), nullable=True)
    new_value = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    ticket = db.relationship("Ticket", back_populates="history")
    user = db.relationship("User")


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    ticket = db.relationship("Ticket", back_populates="comments")
    user = db.relationship("User")


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    file_name = db.Column(db.String(255), nullable=False)
    stored_file_name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    ticket = db.relationship("Ticket", back_populates="attachments")
    uploaded_by = db.relationship("User")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=True)
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user = db.relationship("User")
    ticket = db.relationship("Ticket")


class EmailLog(db.Model):
    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(150), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    error_message = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    
    
class UserDelegation(db.Model):
    """Time-bound out-of-office coverage: while active, tickets destined
    for the delegator are auto-routed to the delegatee, and the delegatee
    gains full view/edit access to the delegator's existing tickets."""
    __tablename__ = "user_delegations"

    id = db.Column(db.Integer, primary_key=True)
    delegator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    delegatee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    end_time = db.Column(db.DateTime(timezone=True), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    start_notified = db.Column(db.Boolean, default=False, nullable=False)
    end_notified = db.Column(db.Boolean, default=False, nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    delegator = db.relationship("User", foreign_keys=[delegator_id], backref="delegations_given")
    delegatee = db.relationship("User", foreign_keys=[delegatee_id], backref="delegations_received")

    def is_currently_active(self):
        if not self.is_active or self.revoked_at is not None:
            return False
        now = utcnow()
        start = to_aware(self.start_time)
        end = to_aware(self.end_time)
        return start <= now <= end

    def status_label(self):
        """One of 'pending', 'active', 'expired', 'revoked' - always
        correct at any moment, regardless of whether the lazy sync job
        has run yet."""
        if self.revoked_at is not None:
            return "revoked"
        now = utcnow()
        start = to_aware(self.start_time)
        end = to_aware(self.end_time)
        if now < start:
            return "pending"
        if now > end:
            return "expired"
        return "active"

    def __repr__(self):
        return f"<UserDelegation {self.delegator_id}->{self.delegatee_id}>"
    
    
    
#here
class Message(db.Model):
    """Simple direct message between two employees, used by the
    'Connections' feature - a lightweight internal inbox, independent
    of tickets."""
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    @staticmethod
    def thread_key(user_a_id, user_b_id):
        """Deterministic pair key so a thread is the same regardless of
        who is viewing it."""
        return tuple(sorted((user_a_id, user_b_id)))


