"""Unit tests for the license + code-availability gate.

Covers pure logic: SPDX classification, license-compat scoring,
GitHub-URL extraction from paper text, and per-class rendering of the
"License & code availability" PR/Issue section. The GitHub-fetch path
(``_fetch_repo_license``) is exercised indirectly via monkeypatching of
``gh_api`` since it's the only network call.

Run with: pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import run  # noqa: E402
from run import Recommendation, Target  # noqa: E402


# ─── _extract_github_urls ─────────────────────────────────────────────────


def test_extract_github_urls_single_match():
    out = run._extract_github_urls("see https://github.com/foo/bar for code")
    assert out == ["foo/bar"]


def test_extract_github_urls_dedup_and_order_preserving():
    text = (
        "https://github.com/foo/bar and again https://github.com/foo/bar, "
        "then https://github.com/baz/qux"
    )
    assert run._extract_github_urls(text) == ["foo/bar", "baz/qux"]


def test_extract_github_urls_strips_dot_git_and_trailing_path():
    text = "code at https://github.com/foo/bar.git/tree/main/sub"
    # .git stripped; everything after the / cut.
    assert run._extract_github_urls(text) == ["foo/bar"]


def test_extract_github_urls_skips_non_repo_owners():
    text = (
        "https://github.com/orgs/remyxai and "
        "https://github.com/topics/vlm should be skipped; "
        "https://github.com/foo/bar should be kept"
    )
    assert run._extract_github_urls(text) == ["foo/bar"]


def test_extract_github_urls_handles_empty_and_none_safely():
    assert run._extract_github_urls("", None, "  ") == []


def test_extract_github_urls_multiple_texts():
    out = run._extract_github_urls(
        "abstract mentions https://github.com/a/one",
        "reasoning mentions https://github.com/b/two",
    )
    assert out == ["a/one", "b/two"]


# ─── _classify_license ────────────────────────────────────────────────────


def test_classify_license_permissive():
    for spdx in ["Apache-2.0", "MIT", "BSD-3-Clause", "ISC", "CC0-1.0"]:
        assert run._classify_license(spdx) == "permissive", spdx


def test_classify_license_copyleft():
    for spdx in ["GPL-3.0", "GPL-2.0", "AGPL-3.0", "MPL-2.0", "CC-BY-SA-4.0"]:
        assert run._classify_license(spdx) == "copyleft", spdx


def test_classify_license_nc():
    for spdx in ["CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0",
                 "CC-BY-NC-SA-3.0"]:
        assert run._classify_license(spdx) == "nc", spdx


def test_classify_license_missing_when_empty():
    assert run._classify_license("") == "missing"
    assert run._classify_license("   ") == "missing"


def test_classify_license_unknown_for_unrecognized():
    # GitHub returns NOASSERTION when it can't parse — _fetch_repo_license
    # collapses that to "", but in case a different caller passes it
    # through, treat it as unknown rather than crashing.
    assert run._classify_license("Custom-License-1.0") == "unknown"
    assert run._classify_license("NOASSERTION") == "unknown"


# ─── _license_compat_score ────────────────────────────────────────────────


def test_compat_permissive_paper_into_anything():
    # Permissive paper code can be adopted regardless of target license.
    for target in ["permissive", "copyleft", "nc", "missing", "unknown"]:
        assert run._license_compat_score("permissive", target) == 1.0


def test_compat_missing_is_blocking():
    # No LICENSE = no permission. Worst-case score everywhere.
    for target in ["permissive", "copyleft", "unknown"]:
        assert run._license_compat_score("missing", target) == 0.0


def test_compat_nc_is_near_zero():
    # NC stays adoption-blocking — visible (non-zero) but should sort
    # below every other class.
    assert run._license_compat_score("nc", "permissive") == 0.1
    assert run._license_compat_score("nc", "copyleft") == 0.1


def test_compat_copyleft_depends_on_target():
    # Copyleft into copyleft: fine. Into permissive: license-compat
    # discussion needed — yellow flag, not red.
    assert run._license_compat_score("copyleft", "copyleft") == 0.7
    assert run._license_compat_score("copyleft", "permissive") == 0.5
    assert run._license_compat_score("copyleft", "unknown") == 0.5


def test_compat_unknown_lands_mid():
    # Unknown should be visible (non-zero) but penalized vs permissive.
    assert run._license_compat_score("unknown", "permissive") == 0.5


# ─── _paper_to_recommendation github URL extraction ──────────────────────


def test_paper_to_recommendation_picks_github_url_from_resource_key():
    rec = run._paper_to_recommendation(
        {
            "title": "X",
            "resource_id": "2601.00001v1",
            "relevance_score": 0.9,
            "resource": {
                "abstract": "no link in here",
                "github_url": "https://github.com/foo/bar",
            },
        },
        fallback_interest_name="x",
        interest_context="",
        experiment_history="",
    )
    assert rec.paper_github_url == "https://github.com/foo/bar"


def test_paper_to_recommendation_falls_back_to_text_scrape():
    rec = run._paper_to_recommendation(
        {
            "title": "X",
            "resource_id": "2601.00002v1",
            "relevance_score": 0.9,
            "reasoning": "code at https://github.com/baz/qux/tree/main",
            "resource": {"abstract": ""},
        },
        fallback_interest_name="x",
        interest_context="",
        experiment_history="",
    )
    assert rec.paper_github_url == "https://github.com/baz/qux"


def test_paper_to_recommendation_no_url_leaves_field_empty():
    rec = run._paper_to_recommendation(
        {
            "title": "X",
            "resource_id": "2601.00003v1",
            "relevance_score": 0.9,
            "resource": {"abstract": "no links anywhere"},
        },
        fallback_interest_name="x",
        interest_context="",
        experiment_history="",
    )
    assert rec.paper_github_url == ""
    # Dataclass defaults intact when no enrichment has run.
    assert rec.paper_license == ""
    assert rec.license_class == "unknown"
    assert rec.license_compat == 0.0


# ─── _enrich_candidate_licenses (mocked gh_api) ──────────────────────────


def test_enrich_candidates_populates_fields_and_compat(monkeypatch):
    # Mock gh_api: target repo is Apache-2.0; one candidate repo is
    # CC-BY-NC-SA-4.0, another has no LICENSE (404), a third has no
    # code URL at all. Verify each lands in the right bucket.
    calls: list[str] = []

    def fake_gh_api(method, path, body=None):
        calls.append(path)
        if path == "/repos/example/target-repo/license":
            return {"license": {"spdx_id": "Apache-2.0"}}
        if path == "/repos/example/nc-paper/license":
            return {"license": {"spdx_id": "CC-BY-NC-SA-4.0"}}
        if path == "/repos/example/no-license-paper/license":
            # 404 surfaces as a RuntimeError from gh_api.
            raise RuntimeError(
                "GitHub GET /repos/example/no-license-paper/license "
                "→ HTTP 404"
            )
        raise AssertionError(f"unexpected gh_api path: {path}")

    monkeypatch.setattr(run, "gh_api", fake_gh_api)
    monkeypatch.setattr(run, "_LICENSE_CACHE", {})

    target = Target(repo="example/target-repo", interest_id="iid")
    candidates = [
        Recommendation(
            paper_title="NCPaper", arxiv_id="2512.02541", tier="high",
            z_score=0.0, spec_md="", paper_abstract="", domain_summary="",
            raw_paper_md="",
            paper_github_url="https://github.com/example/nc-paper",
        ),
        Recommendation(
            paper_title="NoLicensePaper", arxiv_id="2511.01010",
            tier="high", z_score=0.0, spec_md="", paper_abstract="",
            domain_summary="", raw_paper_md="",
            paper_github_url="https://github.com/example/no-license-paper",
        ),
        Recommendation(
            paper_title="NoCodePaper", arxiv_id="2510.00100",
            tier="moderate", z_score=0.0, spec_md="", paper_abstract="",
            domain_summary="", raw_paper_md="",
            paper_github_url="",   # no code link surfaced
        ),
    ]

    run._enrich_candidate_licenses(candidates, target)

    # NC license → adoption-blocking score.
    assert candidates[0].paper_license == "CC-BY-NC-SA-4.0"
    assert candidates[0].license_class == "nc"
    assert candidates[0].license_compat == 0.1

    # 404 from GitHub → empty paper_license, classified "missing" so
    # the downstream report surfaces a loud red flag rather than a
    # silent "unknown" — the helper's contract is that no parseable
    # LICENSE is the worst signal we can give.
    assert candidates[1].paper_license == ""
    assert candidates[1].license_class == "missing"
    assert candidates[1].license_compat == 0.0

    # No URL at all → straight to "missing".
    assert candidates[2].license_class == "missing"
    assert candidates[2].license_compat == 0.0

    # Target license was fetched exactly once (cached).
    assert calls.count("/repos/example/target-repo/license") == 1


def test_fetch_repo_license_caches(monkeypatch):
    """Same owner/repo should hit gh_api at most once per process."""
    hits = {"n": 0}

    def fake_gh_api(method, path, body=None):
        hits["n"] += 1
        return {"license": {"spdx_id": "MIT"}}

    monkeypatch.setattr(run, "gh_api", fake_gh_api)
    monkeypatch.setattr(run, "_LICENSE_CACHE", {})

    a = run._fetch_repo_license("foo/bar")
    b = run._fetch_repo_license("foo/bar")
    c = run._fetch_repo_license("foo/bar")
    assert a == b == c == "MIT"
    assert hits["n"] == 1


def test_fetch_repo_license_collapses_noassertion(monkeypatch):
    """GitHub's NOASSERTION (unparseable LICENSE) should become "" so the
    downstream classifier flags it as missing — better to ask for human
    review than to silently bucket it as unknown."""
    def fake_gh_api(method, path, body=None):
        return {"license": {"spdx_id": "NOASSERTION"}}

    monkeypatch.setattr(run, "gh_api", fake_gh_api)
    monkeypatch.setattr(run, "_LICENSE_CACHE", {})

    assert run._fetch_repo_license("foo/bar") == ""


def test_fetch_repo_license_swallows_errors(monkeypatch):
    """Any exception from gh_api must return "" — license fetch must
    never block the pipeline."""
    def fake_gh_api(method, path, body=None):
        raise RuntimeError("simulated 503")

    monkeypatch.setattr(run, "gh_api", fake_gh_api)
    monkeypatch.setattr(run, "_LICENSE_CACHE", {})

    assert run._fetch_repo_license("foo/bar") == ""


# ─── _render_license_section ──────────────────────────────────────────────


def _rec_with(license_class, paper_license="", url="", compat=0.0):
    return Recommendation(
        paper_title="X", arxiv_id="1234.5678", tier="high",
        z_score=0.0, spec_md="", paper_abstract="", domain_summary="",
        raw_paper_md="",
        paper_github_url=url, paper_license=paper_license,
        license_class=license_class, license_compat=compat,
    )


def test_render_license_section_permissive_safe_to_adopt():
    s = run._render_license_section(_rec_with(
        "permissive", "Apache-2.0", "https://github.com/foo/bar", 1.0,
    ))
    assert "License & code availability" in s
    assert "Apache-2.0" in s
    assert "Permissive" in s
    assert "🟢" in s


def test_render_license_section_nc_is_adoption_blocked():
    s = run._render_license_section(_rec_with(
        "nc", "CC-BY-NC-SA-4.0", "https://github.com/avggt/code", 0.1,
    ))
    assert "adoption blocked" in s.lower()
    assert "CC-BY-NC-SA-4.0" in s
    assert "🔴" in s


def test_render_license_section_missing_is_loud():
    s = run._render_license_section(_rec_with("missing", "", "", 0.0))
    assert "No LICENSE file detected" in s
    assert "blocking" in s.lower()
    assert "🔴" in s


def test_render_license_section_empty_when_no_enrichment():
    # Pristine Recommendation (env opt-out or caller that bypasses
    # query_remyx_candidates) — render to "\n" so PR-body formatting
    # doesn't change.
    rec = Recommendation(
        paper_title="X", arxiv_id="1", tier="high", z_score=0.0,
        spec_md="", paper_abstract="", domain_summary="", raw_paper_md="",
    )
    assert run._render_license_section(rec) == "\n"


# ─── _render_candidate_brief surfaces license info ───────────────────────


def test_candidate_brief_includes_license_line():
    rec = _rec_with("nc", "CC-BY-NC-SA-4.0",
                    "https://github.com/avggt/code", 0.1)
    brief = run._render_candidate_brief([rec])
    assert "code/license:" in brief
    assert "avggt/code" in brief
    assert "CC-BY-NC-SA-4.0" in brief
    assert "(nc, compat=0.10)" in brief


def test_candidate_brief_omits_license_line_when_unset():
    rec = Recommendation(
        paper_title="X", arxiv_id="1", tier="high", z_score=0.0,
        spec_md="", paper_abstract="abstract here",
        domain_summary="", raw_paper_md="",
        reasoning="why", relevance_score=0.9,
    )
    brief = run._render_candidate_brief([rec])
    # Default Recommendation has license_class="unknown", no URL, no license —
    # the brief should suppress the license line entirely.
    assert "code/license:" not in brief
