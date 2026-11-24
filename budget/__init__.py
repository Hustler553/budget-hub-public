import hmac
import secrets
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, abort, redirect, request, session, url_for
from markupsafe import Markup

from budget import db
from budget.services.sources import email_ingest
from budget.services.sources import plaid
from budget.services import accounts
from budget.services import alerts
from budget.services import auth, branding, features, glyphs
from budget.services import recurring
from budget.services import theme
from budget.utils import (bp_to_percent_str, bp_to_rate_str, cents_to_str,
                          e4_to_rate_str, e8_to_str)


def _csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["_csrf_token"] = token
    return token


def create_app():
    app = Flask(__name__)
    db.init_db()

    key = db.get_setting("secret_key")
    if not key:
        key = secrets.token_hex(32)
        db.set_setting("secret_key", key)
    app.secret_key = key

    app.config.update(
        SESSION_COOKIE_NAME=config.SESSION_COOKIE,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=timedelta(days=auth.SESSION_DAYS),
        SESSION_COOKIE_SECURE=config.HTTPS_ONLY,
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    )

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            token = session.get("_csrf_token", "")
            sent = request.form.get("csrf_token", "")
            if not token or not hmac.compare_digest(token, sent):
                abort(400, description="Missing or invalid CSRF token — "
                                       "reload the page and try again.")

    _OPEN_ENDPOINTS = {"auth.setup", "auth.login", "static",
                       "pwa.service_worker", "pwa.offline"}

    @app.before_request
    def require_login():
        if request.endpoint in _OPEN_ENDPOINTS:
            return None
        if request.endpoint is None:
            return None
        if not auth.password_is_set():
            return redirect(url_for("auth.setup"))
        if auth.session_is_live(session):
            return None
        remembered, member_id = auth.device_member(
            request.cookies.get(auth.DEVICE_COOKIE, ""))
        if remembered:
            auth.start_session(session, member_id)
            return None
        target = request.full_path.rstrip("?") if request.method == "GET" else None
        return redirect(url_for("auth.login", next=target))

    @app.context_processor
    def inject_features():
        return {"features": features.all_states()}

    @app.context_processor
    def inject_csrf():
        return {"csrf_field": lambda: Markup(
            f'<input type="hidden" name="csrf_token" value="{_csrf_token()}">')}

    @app.context_processor
    def inject_theme():
        return {"theme": theme.current_theme()}

    @app.context_processor
    def inject_glyphs():
        return {"glyph": glyphs.chip, "glyphs": glyphs}

    @app.context_processor
    def inject_branding():
        return {"app_name": branding.APP_NAME, "wordmark": branding.wordmark()}

    @app.context_processor
    def inject_person():
        member_id = auth.session_member_id(session)
        return {"person": {
            "is_owner": member_id is None,
            "name": (session.get(auth.SESSION_NAME_KEY, "") if member_id is not None
                     else branding.owner_name() or "Owner"),
        }}

    PLAID_CDN = "https://cdn.plaid.com"
    PLAID_API = config.PLAID_HOSTS[config.PLAID_ENV]

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()")
        response.headers.setdefault("Content-Security-Policy", "; ".join([
            "default-src 'self'",
            f"script-src 'self' 'unsafe-inline' {PLAID_CDN}",
            "worker-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "style-src-elem 'self' 'unsafe-inline'",
            "style-src-attr 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            f"connect-src 'self' {PLAID_CDN} {PLAID_API}",
            f"frame-src {PLAID_CDN}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]))
        return response

    from budget.routes.auth import bp as auth_bp
    from budget.routes.dashboard import bp as dashboard_bp
    from budget.routes.household import bp as household_bp
    from budget.routes.transactions import bp as transactions_bp
    from budget.routes.accounts import bp as accounts_bp
    from budget.routes.budget import bp as budget_bp
    from budget.routes.bills import bp as bills_bp
    from budget.routes.paycheck import bp as paycheck_bp
    from budget.routes.moneymath import bp as moneymath_bp
    from budget.routes.pwa import bp as pwa_bp
    from budget.routes.reports import bp as reports_bp
    from budget.routes.settings import bp as settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(household_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(bills_bp)
    app.register_blueprint(paycheck_bp)
    app.register_blueprint(moneymath_bp)
    app.register_blueprint(pwa_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(settings_bp)

    app.template_filter("money")(cents_to_str)
    app.template_filter("money_signed")(lambda c: cents_to_str(c, with_sign=True))
    app.template_filter("percent")(bp_to_percent_str)
    app.template_filter("rate")(bp_to_rate_str)
    app.template_filter("qty")(e8_to_str)
    app.template_filter("rate_e4")(e4_to_rate_str)

    return app


def start_scheduler():
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(recurring.post_due_recurring, "cron", hour=0, minute=5,
                      id="post_recurring", misfire_grace_time=None, coalesce=True)
    scheduler.add_job(accounts.take_balance_snapshots, "cron", hour=0, minute=10,
                      id="balance_snapshots", misfire_grace_time=None,
                      coalesce=True)
    scheduler.add_job(db.backup_db, "cron", hour=0, minute=20,
                      id="backup_db", misfire_grace_time=None, coalesce=True)
    scheduler.add_job(email_ingest.poll_safely, "cron", hour="6-23/2", minute=0,
                      id="poll_email", misfire_grace_time=None, coalesce=True,
                      next_run_time=datetime.now())
    scheduler.add_job(alerts.send_digest, "cron", hour=7, minute=0,
                      id="budget_digest", misfire_grace_time=None, coalesce=True)
    scheduler.add_job(plaid.sync_all, "cron", hour="2,8,14,20", minute=15,
                      id="plaid_sync", misfire_grace_time=None, coalesce=True)
    scheduler.start()
    return scheduler
