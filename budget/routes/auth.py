from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, make_response, redirect,
                   render_template, request, session, url_for)

from budget import config
from budget.services import auth

bp = Blueprint("auth", __name__)


def _safe_next(raw):
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None
    if "\\" in raw:
        return None
    parsed = urlparse(raw)
    if parsed.netloc or parsed.scheme:
        return None
    return raw


def _remember_this_device(response, label, member_id=None):
    raw = auth.issue_device_token(label, member_id=member_id)
    response.set_cookie(
        auth.DEVICE_COOKIE, raw,
        max_age=auth.DEVICE_TOKEN_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="Lax",
        secure=config.HTTPS_ONLY,
    )
    return response


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "GET":
        if auth.password_is_set():
            abort(404)
        return render_template("setup.html",
                               min_length=auth.MIN_PASSWORD_LENGTH)

    won = False
    if not auth.password_is_set():
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if password != confirm:
            flash("Those two passwords do not match.", "error")
            return redirect(url_for("auth.setup"))
        try:
            won = auth.claim_password(password)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("auth.setup"))

    if won:
        auth.start_session(session)
        return redirect(url_for("dashboard.index"))
    flash("Someone else finished setting this instance up first. "
          "Log in with that password.", "error")
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        member = auth.find_member(request.form.get("name", ""))
        if member is not None:
            member_id = member["id"]
            accepted = auth.check_member_password(
                member_id, password, source_ip=request.remote_addr)
        else:
            member_id = None
            accepted = auth.check_password(password,
                                           source_ip=request.remote_addr)
        if accepted:
            auth.start_session(session, member_id)
            target = _safe_next(request.args.get("next")) or url_for("dashboard.index")
            response = make_response(redirect(target))
            if request.form.get("remember"):
                _remember_this_device(
                    response, request.user_agent.string[:80] or "Unknown device",
                    member_id)
            return response
        flash("That password is not right.", "error")
        return redirect(url_for(
            "auth.login", next=_safe_next(request.args.get("next"))))
    return render_template("login.html",
                           locked_for=auth.lockout_remaining(),
                           ask_name=auth.has_members(),
                           device_days=auth.DEVICE_TOKEN_DAYS)


@bp.route("/logout", methods=["POST"])
def logout():
    auth.revoke_token(request.cookies.get(auth.DEVICE_COOKIE, ""))
    auth.revoke_session(session)
    session.clear()
    response = make_response(redirect(url_for("auth.login")))
    response.delete_cookie(auth.DEVICE_COOKIE)
    return response


@bp.route("/devices")
def devices():
    return render_template("devices.html", devices=auth.list_devices(
        member_id=auth.session_member_id(session)))


@bp.route("/devices/<int:device_id>/revoke", methods=["POST"])
def revoke_device(device_id):
    if auth.device_belongs_to(device_id, auth.session_member_id(session)):
        auth.revoke_device(device_id)
    flash("That device will have to log in again.", "ok")
    return redirect(url_for("auth.devices"))
