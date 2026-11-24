import hashlib
import json

from flask import Blueprint, current_app, render_template, url_for

from budget.services import features

bp = Blueprint("pwa", __name__)

PRECACHE_SKIP = {"icon-1024.png"}

PRECACHE_BUDGET_BYTES = 1_000_000


def _static_root():
    return current_app.static_folder


def precache_files():
    from pathlib import Path
    root = Path(_static_root())
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name not in PRECACHE_SKIP
    )


def precache_version(files=None):
    from pathlib import Path
    files = precache_files() if files is None else files
    root = Path(_static_root())
    digest = hashlib.sha256()
    for relative in files:
        digest.update(relative.encode())
        digest.update((root / relative).read_bytes())
    offline = Path(current_app.root_path) / "templates" / "offline.html"
    digest.update(offline.read_bytes())
    return digest.hexdigest()[:12]


@bp.route("/sw.js")
def service_worker():
    if not features.enabled("offline"):
        body = render_template("sw-off.js")
    else:
        files = precache_files()
        body = render_template(
            "sw.js",
            version=precache_version(files),
            shell=json.dumps(
                [url_for("static", filename=f) for f in files], indent=2),
            offline_url=url_for("pwa.offline"),
        )
    response = current_app.response_class(body, mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache, max-age=0"
    return response


@bp.route("/offline")
def offline():
    return render_template("offline.html")
