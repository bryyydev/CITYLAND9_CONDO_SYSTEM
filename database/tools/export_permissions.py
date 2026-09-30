"""Write frontend/src/config/permissions.json from backend/app/core/permissions.py.

The React prototype (mock mode) uses this copy to show each role exactly what the
server would allow. In live mode the server sends the user's permissions itself.

    python database/tools/export_permissions.py          write the file
    python database/tools/export_permissions.py --check  exit 1 if the file is stale
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.core.permissions import ANY_SIGNED_IN, PERMISSIONS  # noqa: E402

TARGET = os.path.join(ROOT, "frontend", "src", "config", "permissions.json")


def render():
    data = {"_generated": "by database/tools/export_permissions.py from backend/app/core/permissions.py - do not edit",
            "anySignedIn": sorted(ANY_SIGNED_IN),
            "permissions": {key: sorted(roles) for key, roles in sorted(PERMISSIONS.items())}}
    return json.dumps(data, indent=2) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        current = open(TARGET, encoding="utf-8").read() if os.path.exists(TARGET) else ""
        if current != text:
            sys.exit("frontend/src/config/permissions.json is stale. Run: python database/tools/export_permissions.py")
        print("frontend/src/config/permissions.json is up to date.")
    else:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"Wrote {TARGET}")
