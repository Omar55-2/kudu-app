from backend.services import message_service
import os
from datetime import timedelta
from flask import Flask

import backend.config as config
from backend.extensions import bcrypt, db, login_manager
from backend.models import Notification, User

BASE_DIR = os.path.dirname(os.path.abspath(__file__))               # .../backend
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

STATUS_BADGE = {
    "new": "bg-blue-50 text-blue-700 border border-blue-100",
    "open": "bg-indigo-50 text-indigo-700 border border-indigo-100",
    "in_progress": "bg-amber-50 text-amber-700 border border-amber-100",
    "waiting": "bg-orange-50 text-orange-700 border border-orange-100",
    "on_hold": "bg-purple-50 text-purple-700 border border-purple-100",
    "resolved": "bg-emerald-50 text-emerald-700 border border-emerald-100",
    "reopened": "bg-pink-50 text-pink-700 border border-pink-100",
    "closed": "bg-gray-100 text-gray-600 border border-gray-200",
    "cancelled": "bg-red-50 text-red-700 border border-red-100",
}
STATUS_DOT = {
    "new": "bg-blue-400", "open": "bg-indigo-500", "in_progress": "bg-amber-500",
    "waiting": "bg-orange-500", "on_hold": "bg-purple-500", "resolved": "bg-emerald-500",
    "reopened": "bg-pink-500", "closed": "bg-gray-400", "cancelled": "bg-red-500",
}
PRIORITY_BADGE = {
    "low": "bg-gray-100 text-gray-600",
    "medium": "bg-blue-50 text-blue-700",
    "high": "bg-amber-50 text-amber-700",
    "urgent": "bg-orange-50 text-orange-700",
    "critical": "bg-red-50 text-red-700",
}
PRIORITY_DOT = {
    "low": "bg-gray-400", "medium": "bg-blue-500", "high": "bg-amber-500",
    "urgent": "bg-orange-500", "critical": "bg-red-500",
}

def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(FRONTEND_DIR, "templates"),
        static_folder=os.path.join(FRONTEND_DIR, "static"),
    )
    app.config.from_object(config)
    app.permanent_session_lifetime = config.PERMANENT_SESSION_LIFETIME

    db.init_app(app)
    from datetime import timedelta as _td

    @app.template_filter('riyadh')
    def riyadh_time(dt):
        if not dt:
            return ""
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return (dt + _td(hours=3)).strftime('%Y-%m-%d %H:%M')
    bcrypt.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from backend.routes.auth_routes import auth_bp
    from backend.routes.ticket_routes import tickets_bp
    from backend.routes.admin_routes import admin_bp
    from backend.routes.team_routes import team_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(team_bp)

    @app.before_request
    def _sync_delegations():
        from flask_login import current_user
        from backend.services import delegation_service
        if current_user.is_authenticated:
            delegation_service.sync_delegations()

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        unread_count = 0
        unread_notifications = []
        if current_user.is_authenticated:
            unread_q = Notification.query.filter_by(
                user_id=current_user.id, is_read=False
            ).order_by(Notification.created_at.desc())
            unread_count = unread_q.count()
            unread_notifications = unread_q.limit(8).all()
        unread_messages = 0
        if current_user.is_authenticated:
            unread_messages = message_service.unread_count(current_user)
        return {
            "status_labels": config.STATUS_LABELS,
            "priority_labels": config.PRIORITY_LABELS,
            "sla_labels": config.SLA_LABELS,
            "unread_notif_count": unread_count,
            "unread_notifications": unread_notifications,
            "brand_name": "Kudu",
            "status_badge": STATUS_BADGE,
            "status_dot": STATUS_DOT,
            "priority_badge": PRIORITY_BADGE,
            "priority_dot": PRIORITY_DOT,
        }

    @app.cli.command("init-db")
    def init_db_command():
        """Create all tables (flask --app backend.app init-db)."""
        db.create_all()
        print("Database tables created.")

    return app

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=5007) 
    

