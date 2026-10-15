"""Provenance: does a page carry material read from an archive (shape 1).

The cold start reads archives (an earlier system's pages) as evidence and drafts confirmations
from them. With `routing.archives` off (the default), the understanding steps (`brief.py`,
`distill.py`) leave such pages out, so a subject's brief and its pages are built from this
space's own records alone, and an owner can measure how the space does without the archive.

    from provenance import is_archive_derived
"""
from __future__ import annotations


def is_archive_derived(fm: dict) -> bool:
    """True for a page the cold start drafted, one that names an archive as its source, or a
    witness page whose text was imported from an archive's page instead of read from the record."""
    fm = fm or {}
    if fm.get("imported_from"):
        return True
    gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
    if str(gen.get("by") or "") == "cold-start":
        return True
    if str(fm.get("source") or "").startswith("archive"):
        return True
    return any(str(item).startswith("archive:") for item in (fm.get("evidence") or []))
