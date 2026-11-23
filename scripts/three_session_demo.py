"""Demonstration of a three-session workflow."""

import os


ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")


def get_usage_headers() -> dict[str, str]:
    if not ADMIN_TOKEN:
        raise RuntimeError("ADMIN_TOKEN is required to access /admin/usage")

    return {"X-Admin-Token": ADMIN_TOKEN}
