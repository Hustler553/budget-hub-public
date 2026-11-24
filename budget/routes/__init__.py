from urllib.parse import urlsplit

from flask import request


def came_from(default):
    parts = urlsplit(request.referrer or "")
    path = parts.path
    if (parts.netloc != request.host or not path.startswith("/")
            or path.startswith("//") or "\\" in path):
        return default
    return path + ("?" + parts.query if parts.query else "")
