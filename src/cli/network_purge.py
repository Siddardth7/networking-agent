"""
src/cli/network_purge.py — GDPR Article 17 hard-delete for networking-agent.

Traceability: DESIGN.md §8.8

Usage (via CLI dispatcher):
    /network-purge --contact <id>
    /network-purge --company <slug>
    /network-purge --all --confirm

Run standalone:
    python -m src.cli.network_purge --contact 42
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.core.db import get_connection, init_db, with_writer

# ---------------------------------------------------------------------------
# Default paths — overridable in tests via _db_path / _log_path / _drafts_dir
# ---------------------------------------------------------------------------
_DEFAULT_LOG_PATH: Path = Path.home() / ".networking-agent" / "purge.log"
_DEFAULT_DRAFTS_DIR: Path = Path.home() / ".networking-agent" / "drafts"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _append_audit(log_path: Path, line: str) -> None:
    """Append *line* (without trailing newline) to the audit log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _safe_rmtree(path: Path) -> bool:
    """Remove *path* recursively, refusing to follow symlinks.

    Returns True if the directory was removed, False if refused or absent.

    Security: ``shutil.rmtree`` follows symlinks by default, which can lead
    to unintentional destruction of the symlink *target* (e.g. if
    ``~/.networking-agent/drafts`` is a symlink to ``~/Documents``). We
    refuse such cases and print a stderr warning so the operator can
    inspect manually. DB-level deletions still proceed.
    """
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink():
        print(
            f"Refusing to remove {str(path)!r}: it is a symlink. "
            "Inspect manually and remove by hand if intended.",
            file=sys.stderr,
        )
        return False
    # Defensive: `is_dir()` follows symlinks, but the earlier early-return
    # already eliminated that case. Re-asserting `not path.is_symlink()`
    # keeps the helper safe against future refactors that reorder branches.
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return True
    return False


def _purge_contact(contact_id: int) -> None:
    """Hard-delete one contact and all dependent rows."""
    with with_writer() as conn:
        # Delete dependents first (outreach_log, drafts), then the contact.
        conn.execute(
            "DELETE FROM outreach_log WHERE contact_id = ?", (contact_id,)
        )
        conn.execute(
            "DELETE FROM drafts WHERE contact_id = ?", (contact_id,)
        )
        conn.execute(
            "DELETE FROM contacts WHERE id = ?", (contact_id,)
        )


def _purge_company(slug: str, drafts_dir: Path) -> tuple[bool, bool]:
    """Hard-delete all contacts (and their dependents) for a company slug.

    Also removes the draft artifact directory at ``drafts_dir/<slug>/``.

    Returns
    -------
    (db_purged, fs_purged):
        ``db_purged`` is True if the company existed and its rows were
        deleted; ``fs_purged`` is True if the drafts subdirectory was
        actually removed (False if it was a symlink and refused, or
        absent). Callers use this to report a truthful summary and
        audit-log entry.
    """
    with with_writer() as conn:
        row = conn.execute(
            "SELECT id FROM companies WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            # Nothing to delete — not an error.
            return False, False
        company_id: int = row["id"]

        # Collect all contact IDs for this company.
        contact_rows = conn.execute(
            "SELECT id FROM contacts WHERE company_id = ?", (company_id,)
        ).fetchall()
        contact_ids: list[int] = [r["id"] for r in contact_rows]

        for cid in contact_ids:
            conn.execute(
                "DELETE FROM outreach_log WHERE contact_id = ?", (cid,)
            )
            conn.execute(
                "DELETE FROM drafts WHERE contact_id = ?", (cid,)
            )

        conn.execute(
            "DELETE FROM contacts WHERE company_id = ?", (company_id,)
        )
        conn.execute(
            "DELETE FROM companies WHERE id = ?", (company_id,)
        )

    # Remove draft artifacts directory (outside the DB transaction).
    slug_dir = drafts_dir / slug
    fs_purged = _safe_rmtree(slug_dir)
    return True, fs_purged


def _purge_all(drafts_dir: Path) -> bool:
    """Hard-delete every row from contacts, drafts, outreach_log, and companies.

    Returns True if the drafts directory was removed, False if it was a
    symlink (refused) or absent.
    """
    with with_writer() as conn:
        conn.execute("DELETE FROM outreach_log")
        conn.execute("DELETE FROM drafts")
        conn.execute("DELETE FROM contacts")
        conn.execute("DELETE FROM companies")

    # Remove the entire drafts directory.
    return _safe_rmtree(drafts_dir)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_purge(
    args: argparse.Namespace,
    _db_path: Path | None = None,
    _log_path: Path | None = None,
    _drafts_dir: Path | None = None,
) -> int:
    """Execute the requested purge operation.

    Parameters
    ----------
    args:
        Parsed CLI namespace.  Expected attributes:
        ``contact`` (int | None), ``company`` (str | None),
        ``all`` (bool), ``confirm`` (bool).
    _db_path:
        Override the SQLite database path (used in tests).
    _log_path:
        Override the audit log path (used in tests).
    _drafts_dir:
        Override the drafts root directory (used in tests).

    Returns
    -------
    int
        0 on success, 1 on refusal or error.
    """
    import src.core.db as db_module  # noqa: PLC0415

    # Apply test overrides before any DB access.
    if _db_path is not None:
        db_module._DB_PATH = _db_path
        init_db()

    log_path = _log_path if _log_path is not None else _DEFAULT_LOG_PATH
    drafts_dir = _drafts_dir if _drafts_dir is not None else _DEFAULT_DRAFTS_DIR

    contact_id: int | None = getattr(args, "contact", None)
    company_slug: str | None = getattr(args, "company", None)
    purge_all: bool = getattr(args, "all", False)
    confirm: bool = getattr(args, "confirm", False)

    # --- Guard: exactly one target must be supplied ---
    targets_given = sum([
        contact_id is not None,
        company_slug is not None,
        purge_all,
    ])

    if targets_given == 0:
        print(
            "Error: specify a target — "
            "--contact <id>, --company <slug>, or --all --confirm."
        )
        return 1

    # --- Guard: --all requires --confirm ---
    if purge_all and not confirm:
        print("Use --all --confirm to purge all data.")
        return 1

    # --- Execute the requested purge ---
    try:
        if contact_id is not None:
            _purge_contact(contact_id)
            audit_line = (
                f"{_iso_now()} | purged contact={contact_id} reason=user-request"
            )
            _append_audit(log_path, audit_line)
            print(f"Purged contact {contact_id}.")

        elif company_slug is not None:
            db_purged, fs_purged = _purge_company(company_slug, drafts_dir)
            fs_note = "fs=ok" if fs_purged else "fs=symlink-skipped"
            audit_line = (
                f"{_iso_now()} | purged company={company_slug} "
                f"reason=user-request {fs_note}"
            )
            _append_audit(log_path, audit_line)
            if db_purged and not fs_purged and (drafts_dir / company_slug).is_symlink():
                print(
                    f"Purged company {company_slug!r}. "
                    "Drafts directory was a symlink and was left in place; "
                    "see stderr."
                )
            else:
                print(f"Purged company {company_slug!r}.")

        else:  # purge_all
            fs_purged = _purge_all(drafts_dir)
            fs_note = "fs=ok" if fs_purged else "fs=symlink-skipped"
            audit_line = (
                f"{_iso_now()} | purged all reason=user-request {fs_note}"
            )
            _append_audit(log_path, audit_line)
            if not fs_purged and drafts_dir.is_symlink():
                print(
                    "Purged all data. "
                    "Drafts directory was a symlink and was left in place; "
                    "see stderr."
                )
            else:
                print("Purged all data.")

    except Exception as exc:  # noqa: BLE001
        print(f"Error during purge: {exc}")
        return 1

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="network-purge",
        description="GDPR Article 17 hard-delete for networking-agent data.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--contact",
        type=int,
        metavar="ID",
        help="Hard-delete a single contact by ID.",
    )
    group.add_argument(
        "--company",
        metavar="SLUG",
        help="Hard-delete all data for a company slug.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        dest="all",
        help="Hard-delete ALL data (requires --confirm).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required when using --all.",
    )
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
    sys.exit(run_purge(_args))
