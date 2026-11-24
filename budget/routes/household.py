from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)

from budget.services import auth, branding
from budget.utils import to_int

bp = Blueprint("household", __name__, url_prefix="/household")


def owner_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if auth.session_member_id(session) is not None:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def _back():
    return redirect(url_for("household.index"))


@bp.route("/")
def index():
    return render_template(
        "household.html",
        members=auth.members(),
        owner_name=branding.owner_name() or "Owner",
        min_length=auth.MIN_PASSWORD_LENGTH,
        max_name=auth.MAX_MEMBER_NAME,
    )


@bp.route("/add", methods=["POST"])
@owner_only
def add():
    name = request.form.get("name", "")
    try:
        auth.add_member(name, request.form.get("password", ""),
                        owner_name=branding.owner_name())
    except ValueError as refused:
        flash(str(refused))
        return _back()
    flash(f"{' '.join(name.split())} can now log in with that name and password. "
          "Ask them to change the password to one of their own.")
    return _back()


@bp.route("/<int:member_id>/remove", methods=["POST"])
@owner_only
def remove(member_id):
    member = auth.get_member(member_id)
    if member is not None:
        auth.remove_member(member_id)
        flash(f"{member['name']} can no longer log in, and is signed out "
              "everywhere.")
    return _back()


@bp.route("/<int:member_id>/password", methods=["POST"])
@owner_only
def reset_member_password(member_id):
    member = auth.get_member(member_id)
    if member is None:
        return _back()
    try:
        auth.set_member_password(member_id, request.form.get("password", ""))
    except ValueError as refused:
        flash(str(refused))
        return _back()
    flash(f"{member['name']} has a new password and is signed out everywhere. "
          "Ask them to change it to one of their own.")
    return _back()


@bp.route("/password", methods=["POST"])
def change_password():
    new = request.form.get("new_password", "")
    if new != request.form.get("confirm", ""):
        flash("Those two new passwords do not match.")
        return _back()
    member_id = auth.session_member_id(session)
    try:
        auth.change_own_password(member_id, request.form.get("current", ""), new)
    except ValueError as refused:
        flash(str(refused))
        return _back()
    auth.start_session(session, member_id)
    flash("Password changed. Your other devices have been signed out.")
    return _back()
