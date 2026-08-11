"""
Kudu Ticketing - central configuration and shared constants.
Single config module (not a class hierarchy) - one deployment, kept simple.
"""
import os
from datetime import timedelta

SECRET_KEY = os.environ.get("SECRET_KEY", "kudu-ticketing-secret-change-in-production")

# Database (MariaDB via PyMySQL)
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Ss79317931")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "kudu_ticketing")
 
SQLALCHEMY_DATABASE_URI = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
) 
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

# Mail (SMTP - point this at Mailtrap for testing, or your real provider)
MAIL_SERVER = os.environ.get("MAIL_SERVER", "sandbox.smtp.mailtrap.io")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "2525"))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "4dbce9dc45fff3")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "031f81da762059")
MAIL_SENDER = os.environ.get("MAIL_SENDER", "no-reply@kudu.local")
MAIL_USE_TLS = True

# Uploads
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
MAX_CONTENT_LENGTH = 10 * 1024 * 1024           #10MB
ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "pdf", "doc", "docx",
    "xls", "xlsx", "txt", "csv", "zip", "log",
}

# Roles & org structure
ROLES = ["admin", "employee"]
DEPARTMENTS = ["IT", "HR", "Finance", "Sales", "Operations", "Support"]

# Ticket workflow
STATUSES = [
    "new", "open", "in_progress", "waiting", "on_hold",
    "resolved", "reopened", "closed", "cancelled",
]

STATUS_LABELS = {
    "new": "New",
    "open": "Open",
    "in_progress": "In Progress",
    "waiting": "Waiting on Requester",
    "on_hold": "On Hold",
    "resolved": "Resolved",
    "reopened": "Reopened",
    "closed": "Closed",
    "cancelled": "Cancelled",
}

STATUS_DESCRIPTIONS = {
    "new": "Submitted and waiting to be picked up.",
    "open": "Acknowledged and queued for work.",
    "in_progress": "Someone is actively working on it.",
    "waiting": "Blocked pending a reply from the requester.",
    "on_hold": "Paused for a dependency or scheduled follow-up.",
    "resolved": "Fix delivered - will auto-close if not reopened.",
    "reopened": "Came back after being marked resolved.",
    "closed": "Completed and archived.",
    "cancelled": "Withdrawn or invalid request.",
}

# Allowed next statuses for a normal (non-admin) status change
VALID_TRANSITIONS = {
    "new": {"open", "cancelled"},
    "open": {"in_progress", "on_hold", "waiting", "cancelled"},
    "in_progress": {"waiting", "on_hold", "resolved", "cancelled"},
    "waiting": {"in_progress", "on_hold", "resolved", "cancelled"},
    "on_hold": {"in_progress", "waiting", "resolved", "cancelled"},
    "resolved": {"closed", "reopened"},
    "reopened": {"open", "in_progress", "cancelled"},
    "closed": {"reopened"},
    "cancelled": set(),
}

STATUS_RANK = {
    "reopened": 0, "new": 1, "open": 2, "in_progress": 3,
    "waiting": 4, "on_hold": 5, "resolved": 6, "closed": 7, "cancelled": 8,
}

PRIORITIES = ["low", "medium", "high", "urgent", "critical"]
PRIORITY_LABELS = {
    "low": "Low", "medium": "Medium", "high": "High",
    "urgent": "Urgent", "critical": "Critical",
}
PRIORITY_RANK = {"critical": 0, "urgent": 1, "high": 2, "medium": 3, "low": 4}

SLA_MINUTES = {
    "low": 72 * 60,
    "medium": 48 * 60,
    "high": 24 * 60,
    "urgent": 8 * 60,
    "critical": 2 * 60,
}
SLA_LABELS = {
    "low": "72 hours", "medium": "48 hours", "high": "24 hours",
    "urgent": "8 hours", "critical": "2 hours",
}

CATEGORIES = ["hardware", "software", "network", "access", "maintenance", "other"]

# A resolved ticket auto-closes if nobody reopens it within this window.
AUTO_CLOSE_AFTER = timedelta(days=3)

EMAIL_VERIFY_EXPIRES = timedelta(hours=24)
PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# --- Team management ---------------------------------------------------
WORKLOAD_WEIGHTS = {"low": 1, "medium": 2, "high": 4, "urgent": 6, "critical": 8}
WORKLOAD_CAPACITY = 20
SLA_RISK_MINUTES = 120  # under this much time left on an active ticket counts as "at risk"
SLA_PRESSURE = {"normal": 0, "at_risk": 2, "breached": 4}
# (low, high, label) - inclusive percentage bands used to describe workload
WORKLOAD_STATES = [(0, 49, "Available"), (50, 74, "Normal"), (75, 89, "High workload"), (90, 10_000, "Overloaded")]

AVAILABILITY_STATES = ["available", "busy", "dnd", "offline", "on_leave"]
# AVAILABILITY_LABELS = {
#     "available": "Available", "busy": "Busy", "dnd": "Do Not Disturb",
#     "offline": "Offline", "on_leave": "On Leave",
# }
AVAILABILITY_LABELS = {
    "available": "Available", "busy": "Busy", "dnd": "Do Not Disturb",
    "offline": "Offline", "on_leave": "On Leave", "delegating": "Delegating",
}
ACTIVE_TICKET_STATUSES = {"open", "in_progress", "waiting", "on_hold", "reopened"}
