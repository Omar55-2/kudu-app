"""
Authentication routes. Registration doubles as employee self-signup: the
first account ever created becomes admin automatically, everyone after
that is a regular employee (an admin can promote someone later from the
admin panel). Every new account must verify its email via a link before
it can log in - there is no OTP/code step anywhere in this flow.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

import backend.config as config
from backend.extensions import db
from backend.models import EmailVerification, User, utcnow, to_aware

from backend.services.email_service import (
    create_verification_token,
    send_verification_email,
    create_password_reset_token,
    verify_password_reset_token,
    send_password_reset_email,
)
from backend.utils import is_valid_email

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("tickets.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("Your account has been deactivated. Contact an administrator.", "error")
            return render_template("auth/login.html")

        if not user.email_verified:
            flash("Please verify your email before logging in.", "warning")
            return redirect(url_for("auth.resend_verification", email=email))

        login_user(user, remember=bool(request.form.get("remember")))
        flash(f"Welcome back, {user.full_name.split(' ')[0]}.", "success")
        next_page = request.args.get("next")
        return redirect(next_page or url_for("tickets.dashboard"))

    return render_template("auth/login.html")

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if current_user.is_authenticated:
        return redirect(url_for("tickets.dashboard"))

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Please enter your email address.", "error")
            return render_template("auth/forgot_password.html")

        user = User.query.filter_by(email=email).first()

        # Do not reveal whether the email exists.
        if user:
            token = create_password_reset_token(user)

            reset_url = url_for(
                "auth.reset_password",
                token=token,
                _external=True
            )

            send_password_reset_email(user, reset_url)

        flash(
            "If an account exists with that email, a password reset link has been sent.",
            "success"
        )

        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")

@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    user = verify_password_reset_token(token)

    if not user:
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password or not confirm_password:
            flash("Both password fields are required.", "error")
            return render_template("auth/reset_password.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset_password.html")

        user.set_password(password)

        db.session.commit()

        flash(
            "Your password has been reset successfully. You can now log in.",
            "success"
        )

        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html")


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("tickets.dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        department = request.form.get("department", "Support")

        form = request.form

        if not full_name or not email or not password:
            flash("All fields are required.", "error")
            return render_template("auth/signup.html", form=form, departments=config.DEPARTMENTS)
        if not is_valid_email(email):
            flash("Please enter a valid email address.", "error")
            return render_template("auth/signup.html", form=form, departments=config.DEPARTMENTS)
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/signup.html", form=form, departments=config.DEPARTMENTS)
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/signup.html", form=form, departments=config.DEPARTMENTS)
        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "error")
            return render_template("auth/signup.html", form=form, departments=config.DEPARTMENTS)
        if department not in config.DEPARTMENTS:
            department = "Support"

        is_first_user = User.query.count() == 0
        user = User(
            full_name=full_name,
            email=email,
            department=department,
            role="admin" if is_first_user else "employee",
            email_verified=False,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        token = create_verification_token(user)
        db.session.commit()

        verify_url = url_for("auth.verify_email", token=token, _external=True)
        send_verification_email(user, verify_url)

        return render_template("auth/verify_notice.html", email=email)

    return render_template("auth/signup.html", form={}, departments=config.DEPARTMENTS)


@auth_bp.route("/verify/<token>")
def verify_email(token):
    record = EmailVerification.query.filter_by(token=token, used=False).first()
    if not record or to_aware(record.expires_at) < utcnow():
        flash("This verification link is invalid or has expired. Request a new one below.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, record.user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("auth.login"))

    user.email_verified = True
    record.used = True
    db.session.commit()
    flash("Email verified successfully. You can now log in.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/resend-verification")
def resend_verification():
    email = (request.args.get("email") or "").strip().lower()
    user = User.query.filter_by(email=email).first()
    if user and not user.email_verified:
        token = create_verification_token(user)
        db.session.commit()
        verify_url = url_for("auth.verify_email", token=token, _external=True)
        send_verification_email(user, verify_url)
        return render_template("auth/verify_notice.html", email=email, resent=True)
    flash("No pending verification found for that email.", "error")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

