"""
src/cli/network_import.py — Import a leads file from any source and (optionally)
draft outreach for every contact.

Traceability: docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md

Normalizes an Apollo export, Apify scrape, Serper / Cowork+Chrome JSON, or a
hand-compiled CSV/JSON into canonical contacts, runs the shared ingest path, and
with --draft marks them SELECTED and drafts immediately. --validate is a dry-run
contract check that writes nothing.

Run standalone:
    python -m src.cli.network_import leads.csv --company "Joby Aviation" --draft
    python -m src.cli.network_import chrome.json --validate
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["run_import"]


def run_import(
    args: argparse.Namespace,
    _import_fn=None,
    _validate_fn=None,
) -> int:
    """Run the import for *args.file* and return an exit code.

    Parameters
    ----------
    args:
        Parsed CLI arguments: ``file`` (str, required), plus optional
        ``company``, ``location``, ``source``, ``draft`` (bool),
        ``validate`` (bool).
    _import_fn / _validate_fn:
        Injected for tests; default to the real importer functions.

    Returns
    -------
    int
        0 on success, 1 on error / failed validation.
    """
    file_path: str | None = getattr(args, "file", None)
    if not file_path:
        print("Error: a leads file path is required", file=sys.stderr)
        return 1

    source = getattr(args, "source", "auto") or "auto"
    company = getattr(args, "company", None)
    location = getattr(args, "location", None)

    # ---- Dry-run validation (writes nothing) -----------------------------
    if getattr(args, "validate", False):
        if _validate_fn is None:
            from src.agents.importer import validate_contacts_file as _validate_fn

        report = _validate_fn(file_path, source, default_company=company)
        status = "OK" if report["ok"] else "FAILED"
        print(f"Validation: {status} — {report['count']} usable contact(s)")
        for err in report["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)
        for warn in report["warnings"]:
            print(f"  warning: {warn}")
        return 0 if report["ok"] else 1

    # ---- Import (+ optional draft) ---------------------------------------
    if _import_fn is None:
        from src.agents.importer import import_contacts as _import_fn

    from src.agents.importer import ContactImportError

    draft = bool(getattr(args, "draft", False))
    try:
        summary = _import_fn(
            file_path,
            company=company,
            location=location,
            source=source,
            auto_select=draft,  # --draft implies select-then-draft
            draft=draft,
        )
    except ContactImportError as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Import failed: file not found: {file_path}", file=sys.stderr)
        return 1

    by_company = summary["by_company"]
    contribution = summary["contribution"]

    # "No silent caps": always surface what the source contributed and dropped.
    dropped = contribution["dropped"]
    drop_bits = [
        f"{n} {label}"
        for label, n in (
            ("no-name", dropped["no_name"]),
            ("no-company", dropped["no_company"]),
            ("duplicate", dropped["duplicate"]),
        )
        if n
    ]
    drop_suffix = f" (dropped: {', '.join(drop_bits)})" if drop_bits else ""
    print(
        f"Source '{contribution['source']}': {contribution['rows_read']} row(s) read "
        f"→ {contribution['usable']} usable{drop_suffix}"
    )

    total_imported = sum(s["imported"] for s in by_company.values())
    total_drafted = sum(s["drafted"] for s in by_company.values())
    print(f"Imported {total_imported} contact(s) across {len(by_company)} company(ies):")
    for slug, s in by_company.items():
        line = f"  {slug}: {s['imported']} imported"
        if draft:
            line += f", {s['drafted']} drafts generated"
        print(line)
    if draft:
        print(f"Total drafts: {total_drafted}. Review with /network-status or the artifact.")
    else:
        print("Contacts imported (state=NEW). Run the selection gate, or re-run with --draft.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import a leads file (Apollo/Apify/Serper/Cowork+Chrome/manual) and draft."
    )
    parser.add_argument("file", help="Path to the leads file (.csv or .json)")
    parser.add_argument(
        "--company",
        default=None,
        help="Default company name/slug when the file has no company column",
    )
    parser.add_argument("--location", default=None, help="Default location context")
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "apollo", "apify", "serper", "chrome", "manual"],
        help="Input format override (default: auto-detect by extension)",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Mark imported contacts SELECTED and draft them immediately",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Dry-run: report parse errors/warnings without writing anything",
    )
    parsed = parser.parse_args()
    sys.exit(run_import(parsed))
