"""Dry-run ("check only") of a data-migration workbook from the command line. NOTHING IS WRITTEN.

    .venv\\Scripts\\python.exe database\\import_check.py <workbook.xlsx> [--config FILE] [--database NAME] [--details FILE.csv] [--json]

  --config FILE     decisions file (default: migration_data\\import_config.json; template:
                    database\\import_config.example.json)
  --database NAME   check against another database on this PC's server, e.g. a staging copy
                    restored with database\\restore.py --into cityland9_stage (recommended)
  --details FILE    write every issue (sheet, row, column, code, severity) to a CSV. Keep it in
                    migration_data\\ (git-ignored). It contains no workbook values.
  --as-migration    treat a CITYLAND9 export layout as data from ANOTHER system (crosswalk ids) instead of
                    an edit of this database's own export (the default for that layout)
  --json            print the whole report as JSON instead of the summary

Exit code: 0 = no blocking issue found (importing is still NOT enabled), 1 = blocked / needs
decisions, 2 = the file couldn't be read. Guide: CITYLAND9_IMPORT_SAFEGUARDS.md.
The workbook is opened read-only and never changed. No database row, backup, audit entry or
account is created (a guard refuses any write statement while the check runs).
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "database"))


def arg(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            sys.exit(f"{flag} needs a value.")
        return args[i + 1]
    return None


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    workbook = os.path.abspath(args[0])
    if not os.path.isfile(workbook):
        sys.exit(f"No such file: {workbook}")
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:
        pass
    if arg(args, "--database"):
        os.environ["MYSQL_DATABASE"] = arg(args, "--database")
    if os.getenv("DB_ENGINE", "").strip().lower() == "mysql" and os.getenv("MYSQL_DATA_DIR") and not os.getenv("DATABASE_URL"):
        import local_mysql
        local_mysql.start()
    from app.core import security
    try:
        import legacy_app as m
    except security.ConfigurationError as exc:
        sys.exit(f"Configuration error: {exc}")
    from app.services import import_check

    config_path = arg(args, "--config") or os.path.join(ROOT, "migration_data", "import_config.json")
    config = import_check.load_config(config_path)
    with m.app.app_context(), open(workbook, "rb") as fh:
        try:
            report = import_check.check_workbook(fh, workbook, legacy=m, config=config,
                                                 roundtrip=False if "--as-migration" in args else "auto")
        except import_check.CheckError as exc:
            print(f"Can't check this file: {exc}")
            return 2
    uri = m.app.config.get("SQLALCHEMY_DATABASE_URI", "")
    target = uri.rsplit("/", 1)[-1].split("?")[0] if uri.startswith("mysql") else ("SQLite file" if uri.startswith("sqlite") else "other")

    details = arg(args, "--details")
    if details:
        with open(details, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["sheet", "row", "column", "code", "severity", "message"])
            for i in report["issues"]:
                writer.writerow([i["sheet"], i["row"], i["column"], i["code"], i["severity"], i["message"]])

    if "--json" in args:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"CHECK ONLY - nothing was written. Database read: {target}. Config: {'loaded' if config else 'none (template only)'}")
        print(f"File: {report['file']['name']} ({report['file']['bytes']:,} bytes, sha256 {report['file']['sha256'][:16]}...)  format: {report['format']}, mode: {report['mode']}")
        gate = report["importGate"]
        print(f"Import workbook (Settings): {'ALLOWED for this file' if gate['allowed'] else 'LOCKED for this file'}"
              + ("" if gate["allowed"] else ": " + ", ".join(b["code"] for b in gate["blockers"])))
        for p in report["configProblems"]:
            print(f"  CONFIG  {p['code']}: {p['message']}")
        print("\nRows per sheet:")
        for sheet, c in report["sheets"].items():
            print(f"  {sheet:16} {c['rows']:>6} rows: {c['valid']:>6} valid  {c['blocked']:>6} blocked  {c['conflict']:>6} conflict  {c['unresolved']:>6} unresolved")
        if report["proposals"]:
            print("\nProposed (only where the matching rule is confirmed):")
            for entity, p in report["proposals"].items():
                print(f"  {entity:16} insert {p['insert']}, link {p['link']}, update {p['update']}, unchanged {p['unchanged']}")
        print("\nIssues by code:")
        for code, c in sorted(report["issueCounts"].items(), key=lambda kv: (-{"blocked": 3, "conflict": 2, "unresolved": 1, "warning": 0}[kv[1]["severity"]], kv[0])):
            print(f"  {c['severity']:10} {code:36} {c['count']:>6}  {c['message']}")
        print("\nReadiness:")
        for area, r in report["readiness"].items():
            print(f"  {area:12} {'ready' if r['ready'] else 'NOT ready'}" + ("" if r["ready"] else ": " + ", ".join(b["code"] for b in r["blockers"])))
        for note in report["notes"]:
            print(f"Note: {note}")
        if details:
            print(f"\nAll issues written to {details} (sheet/row/column/code only).")
    blocking = any(c["severity"] in ("blocked", "conflict", "unresolved") for c in report["issueCounts"].values())
    return 1 if blocking or any(not r["ready"] for a, r in report["readiness"].items() if a != "import") else 0


if __name__ == "__main__":
    sys.exit(main())
