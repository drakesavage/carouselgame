"""Back up installed SQLite state and atomically import reconciled references."""
import datetime
from contextlib import closing
import json
import pathlib
import sqlite3
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.carousel.importer import run_import
from app.config import Settings

settings = Settings()
settings.ensure_dirs()
with closing(sqlite3.connect(f"file:{settings.campaign_db.as_posix()}?mode=ro", uri=True)) as c:
    active = c.execute("SELECT id,status FROM turns WHERE status IN ('processing','pending','running')").fetchall()
    if active:
        raise RuntimeError(f"Active game turns; import postponed: {active}")
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = settings.data_dir / "backups" / ("asset-reconciliation-" + stamp)
backup.mkdir(parents=True, exist_ok=False)
for original in [settings.campaign_db, settings.reference_db]:
    if original.exists():
        with closing(sqlite3.connect(f"file:{original.as_posix()}?mode=ro", uri=True)) as src, closing(sqlite3.connect(backup / original.name)) as dst:
            src.backup(dst)
for name in ["reference_quarantine.json", "settings.json"]:
    if (settings.data_dir / name).is_file():
        shutil.copy2(settings.data_dir / name, backup / name)
result = run_import(settings)
receipt = {"ok": result.ok, "counts": result.counts, "missing": result.missing, "error": result.error,
           "backup": str(backup), "import_report": str(result.report_path), "campaign_rebuilt": False,
           "human_approval_inferred": False, "illustrated_submaps_installed": False}
(settings.source_dir / "Asset_Reconciliation/installation.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
if not result.ok:
    raise RuntimeError(result.error)
print(json.dumps(receipt, indent=2))
