from provenance import is_archive_derived


def test_cold_start_page_is_archive_derived():
    assert is_archive_derived({"generated": {"by": "cold-start", "at": "2026-10-12T06:42:21Z"}})


def test_archive_evidence_is_archive_derived():
    assert is_archive_derived({"evidence": ["gmail-1", "archive:Old/identity/spine"]})
    assert is_archive_derived({"source": "archive"})


def test_own_records_are_not():
    assert not is_archive_derived({"generated": {"by": "distill"}, "evidence": ["gmail-1"]})
    assert not is_archive_derived({})
    assert not is_archive_derived(None)
