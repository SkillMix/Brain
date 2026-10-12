"""The template's name gate: no token whose sha256 is on the forbidden list may appear."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import no_company_names

REPO_ROOT = Path(__file__).resolve().parents[3]


def _hash(word: str) -> str:
    return hashlib.sha256(word.lower().encode("utf-8")).hexdigest()


def _space(tmp_path: Path, *forbidden: str) -> Path:
    root = tmp_path / "space"
    (root / "_meta" / "tests").mkdir(parents=True)
    lines = ["# sha256 of lowercased tokens", *(_hash(w) for w in forbidden)]
    (root / "_meta" / "tests" / "forbidden-names.sha256").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_forbidden_token_is_reported_with_its_line(tmp_path):
    root = _space(tmp_path, "acmeco")
    (root / "notes.md").write_text(
        "A clean first line.\nWe met AcmeCo on Tuesday.\nThen acme-co wrote back.\n",
        encoding="utf-8")
    assert no_company_names.check(root) == [("notes.md", 2), ("notes.md", 3)]


def test_one_hit_per_line_even_with_several_tokens(tmp_path):
    root = _space(tmp_path, "acmeco")
    (root / "a.md").write_text("AcmeCo and acme_co and ACMECO.\n", encoding="utf-8")
    assert no_company_names.check(root) == [("a.md", 1)]


def test_clean_file_passes(tmp_path):
    root = _space(tmp_path, "acmeco")
    (root / "docs").mkdir()
    (root / "docs" / "clean.md").write_text(
        "Lantern Works ships Harbor for Tidewater Ferries. Acme alone is fine.\n",
        encoding="utf-8")
    assert no_company_names.check(root) == []


def test_skips_git_hashes_file_feed_binaries_and_large_files(tmp_path):
    root = _space(tmp_path, "acmeco")
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "objects" / "x").write_text("acmeco\n", encoding="utf-8")
    (root / "_meta" / "state" / "feed").mkdir(parents=True)
    (root / "_meta" / "state" / "feed" / "mail.jsonl").write_text('{"from": "acmeco"}\n',
                                                                   encoding="utf-8")
    (root / "blob.bin").write_bytes(b"acmeco\x00\x01\x02")
    (root / "big.txt").write_text("acmeco\n" + "x" * (1024 * 1024), encoding="utf-8")
    # the hashes file itself holds the hash, not the name; a name written there must not count
    assert no_company_names.check(root) == []


def test_hashes_path_argument_overrides_default(tmp_path):
    root = _space(tmp_path)  # empty default list
    other = tmp_path / "elsewhere.sha256"
    other.write_text(_hash("acmeco") + "\n", encoding="utf-8")
    (root / "page.md").write_text("AcmeCo again\n", encoding="utf-8")
    assert no_company_names.check(root) == []
    assert no_company_names.check(root, hashes_path=other) == [("page.md", 1)]


def test_missing_hashes_file_means_nothing_forbidden(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "page.md").write_text("AcmeCo\n", encoding="utf-8")
    assert no_company_names.check(root) == []


def test_cli_exit_code_and_output(tmp_path, capsys):
    root = _space(tmp_path, "acmeco")
    (root / "notes.md").write_text("fine\nAcmeCo\n", encoding="utf-8")
    assert no_company_names.main([str(root)]) == 1
    assert capsys.readouterr().out.strip() == "notes.md:2"
    (root / "notes.md").write_text("fine\n", encoding="utf-8")
    assert no_company_names.main([str(root)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_this_repository_passes():
    if not (REPO_ROOT / ".github" / "template-marker").exists():
        pytest.skip("an instance carries its company's names by design; the check is for the template")
    assert no_company_names.check(REPO_ROOT) == []
