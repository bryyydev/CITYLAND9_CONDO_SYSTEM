"""Is frontend/dist the current, LIVE build of frontend/src? Used by START_WINDOWS.bat.

    python scripts/frontend_build.py status   exit 0 = up to date, 1 = outdated, 2 = missing, 3 = not a live build
    python scripts/frontend_build.py stamp    record that dist was just built from the current sources

"Outdated" is decided by CONTENT, not file dates (a git pull or copying the folder changes dates):
a SHA-256 of every source file that affects the build is stored in dist/.source-hash after a
successful build. A build without that file (made before this check existed) counts as outdated.
A build made with `vite build --mode mock` contains the prototype's mock-data banner and is refused.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
DIST = os.path.join(FRONTEND, "dist")
STAMP = os.path.join(DIST, ".source-hash")
SOURCE_DIRS = ("src", "public")
SOURCE_FILES = ("index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json",
                "tsconfig.app.json", "tsconfig.node.json")
MOCK_MARKER = b"Prototype \xc2\xb7 mock data"   # AppShell's prototype banner; only in mock builds


def source_hash():
    digest = hashlib.sha256()
    paths = [os.path.join(FRONTEND, f) for f in SOURCE_FILES if os.path.isfile(os.path.join(FRONTEND, f))]
    for folder in SOURCE_DIRS:
        for base, dirs, files in os.walk(os.path.join(FRONTEND, folder)):
            dirs.sort()
            paths.extend(os.path.join(base, f) for f in files)
    for path in sorted(paths, key=lambda p: os.path.relpath(p, FRONTEND).replace("\\", "/")):
        digest.update(os.path.relpath(path, FRONTEND).replace("\\", "/").encode())
        digest.update(b"\0")
        with open(path, "rb") as fh:
            # Line endings don't change the build; normalise so a CRLF checkout matches an LF one.
            digest.update(fh.read().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def is_mock_build():
    assets = os.path.join(DIST, "assets")
    if not os.path.isdir(assets):
        return False
    for name in os.listdir(assets):
        if name.endswith(".js"):
            with open(os.path.join(assets, name), "rb") as fh:
                if MOCK_MARKER in fh.read():
                    return True
    return False


def status():
    if not os.path.isfile(os.path.join(DIST, "index.html")):
        print("      React app: not built yet.")
        return 2
    if is_mock_build():
        print("      React app: frontend\\dist is a PROTOTYPE (mock data) build, not the live system.")
        return 3
    try:
        with open(STAMP, encoding="ascii") as fh:
            built_from = fh.read().strip()
    except OSError:
        built_from = ""
    if built_from != source_hash():
        print("      React app: the code changed since the last build.")
        return 1
    return 0


def stamp():
    if not os.path.isfile(os.path.join(DIST, "index.html")):
        print("frontend\\dist\\index.html is missing; nothing to stamp.")
        return 2
    if is_mock_build():
        print("frontend\\dist is a prototype (mock data) build; refusing to mark it as the live build.")
        return 3
    with open(STAMP, "w", encoding="ascii") as fh:
        fh.write(source_hash() + "\n")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command not in ("status", "stamp"):
        sys.exit(__doc__)
    sys.exit(status() if command == "status" else stamp())
