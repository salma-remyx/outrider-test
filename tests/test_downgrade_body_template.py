"""Tests for the v1.4.5 downgrade-Issue body template refinements:

  - TL;DR section appears when provided
  - "Why this candidate" selection-note section appears when non-empty
    and not a parenthetical fallback string
  - Suggested experiment is suppressed when caller flags
    suppress_suggested_experiment, replaced when replacement provided
  - "Why this paper" section is skipped when skip_paper_reasoning_section
    (preflight case — preflight's detail already covers it)
  - "What else Outrider considered this run" collapsed details block
    renders when selection_rejected is non-empty
  - footer_override actually substitutes the open_issue boilerplate
  - Section ordering is consistent: header → TL;DR → license → why
    candidate → why paper → suggested experiment → diff → why-this-Issue
    → what-else-considered

Run with: pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import run  # noqa: E402
from run import Recommendation, Target  # noqa: E402


def _rec():
    return Recommendation(
        paper_title="Sample Paper", arxiv_id="2601.00001", tier="high",
        z_score=0.0, spec_md="",
        paper_abstract="abstract text", domain_summary="", raw_paper_md="",
        relevance_score=0.92,
        reasoning="paper anchors on the localize stage",
        suggested_experiment="enable the new flag and benchmark",
        interest_name="ExampleInterest",
    )


def _capture(monkeypatch):
    """Patch open_issue and return a dict that gets populated on call."""
    captured: dict = {}

    def fake_open_issue(target, title, body, **kw):
        captured["title"] = title
        captured["body"] = body
        captured["kwargs"] = kw
        return "https://github.com/example/repo/issues/999"

    monkeypatch.setattr(run, "open_issue", fake_open_issue)
    return captured


# ─── TL;DR section ────────────────────────────────────────────────────────


def test_tldr_renders_at_top_when_provided(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        tldr="One-sentence at-a-glance about this paper.",
    )
    body = captured["body"]
    assert "## TL;DR" in body
    assert "One-sentence at-a-glance" in body
    # Must appear before any other ## heading so the reviewer sees it
    # before reading the rest.
    assert body.index("## TL;DR") < body.index("## Why")


def test_tldr_omitted_when_empty(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
    )
    assert "## TL;DR" not in captured["body"]


def test_tldr_whitespace_only_omitted(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y", tldr="   \n  ",
    )
    assert "## TL;DR" not in captured["body"]


# ─── "Why this candidate" selection-note parity ──────────────────────────


def test_selection_note_renders_when_meaningful(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        selection_note="picked over 30 alternatives because of a clear call site",
    )
    body = captured["body"]
    assert "## Why this candidate (selected from the lookback pool)" in body
    assert "picked over 30 alternatives" in body


def test_selection_note_skipped_for_fallback_parenthetical(monkeypatch):
    """The fallback string ('(selection pass unavailable — used …)')
    is non-informative — the section should be omitted rather than
    rendering a non-explanation."""
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        selection_note="(selection pass unavailable — used highest-relevance candidate as fallback)",
    )
    assert "Why this candidate" not in captured["body"]


def test_selection_note_omitted_when_empty(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
    )
    assert "Why this candidate" not in captured["body"]


# ─── Suggested experiment suppression + replacement ──────────────────────


def test_suggested_experiment_suppressed_when_flagged(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        suppress_suggested_experiment=True,
    )
    body = captured["body"]
    # Original suggestion must not appear when suppressed without
    # replacement — preflight case.
    assert "## Suggested experiment" not in body
    assert "enable the new flag and benchmark" not in body


def test_replacement_experiment_substitutes_original(monkeypatch):
    """When preflight rejects the paper's suggested experiment as
    hollow, it can supply a replacement. The body renders the
    replacement under the same heading, never the original."""
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        suppress_suggested_experiment=True,
        replacement_experiment=(
            "Read the paper's released benchmark and wire it into the "
            "existing eval path instead — viable smaller slice."
        ),
    )
    body = captured["body"]
    assert "## Suggested experiment" in body
    assert "Read the paper's released benchmark" in body
    # Original must not appear — replacement is the whole point.
    assert "enable the new flag and benchmark" not in body


def test_default_renders_original_suggested_experiment(monkeypatch):
    """Without the suppress flag, the original suggestion still
    renders — backwards-compat for non-preflight downgrades."""
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
    )
    body = captured["body"]
    assert "## Suggested experiment" in body
    assert "enable the new flag and benchmark" in body


# ─── skip_paper_reasoning_section (duplicate-Why fix) ────────────────────


def test_skip_paper_reasoning_section_omits_header(monkeypatch):
    """When the preflight Issue body already covers the paper-interest
    angle in its own detail, the scaffolding's header should NOT
    render — that's the v1.4.4 'two Why this paper sections' bug."""
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        skip_paper_reasoning_section=True,
    )
    body = captured["body"]
    assert "## Why this paper is interesting for the team" not in body
    # rec.reasoning string should not have been rendered either
    assert "paper anchors on the localize stage" not in body


def test_default_renders_paper_reasoning_section(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
    )
    body = captured["body"]
    assert "## Why this paper is interesting for the team" in body
    assert "paper anchors on the localize stage" in body


# ─── "What else Outrider considered this run" section ────────────────────


def test_selection_rejected_renders_collapsed_details(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        selection_rejected=[
            {"arxiv_id": "2502.20110", "title": "RejectedA",
             "reason": "no call site"},
            {"arxiv_id": "2502.20111", "title": "RejectedB",
             "reason": "license incompatible"},
        ],
    )
    body = captured["body"]
    assert "## What else Outrider considered this run" in body
    assert "<details>" in body
    assert "RejectedA" in body
    assert "RejectedB" in body
    assert "no call site" in body
    assert "license incompatible" in body
    # Each candidate carries its arxiv link
    assert "https://arxiv.org/abs/2502.20110" in body


def test_selection_rejected_caps_at_10(monkeypatch):
    captured = _capture(monkeypatch)
    rejected = [
        {"arxiv_id": f"2502.{i:05d}", "title": f"Reject{i}", "reason": f"why{i}"}
        for i in range(15)
    ]
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        selection_rejected=rejected,
    )
    body = captured["body"]
    assert "Reject0" in body and "Reject9" in body
    # 10 onwards collapse into "…and N more"
    assert "Reject10" not in body
    assert "…and 5 more" in body


def test_selection_rejected_empty_omits_section(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        selection_rejected=[],
    )
    assert "What else Outrider considered" not in captured["body"]


def test_selection_rejected_none_omits_section(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
    )
    assert "What else Outrider considered" not in captured["body"]


# ─── footer_override threads through open_issue ──────────────────────────


def test_footer_override_threads_through(monkeypatch):
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
        footer_override="_custom footer for this routing reason_",
    )
    # The footer text isn't inside `body` — open_issue appends it
    # itself — but the kwarg must arrive at open_issue intact so the
    # final Issue body picks it up.
    assert captured["kwargs"].get("footer_override") == (
        "_custom footer for this routing reason_"
    )


def test_open_issue_appends_default_footer_when_no_override(monkeypatch):
    """Sanity-check that open_issue still gets called without an
    override on legacy call sites."""
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="x", detail="y",
    )
    # Either the kwarg is missing (older signature) or empty string —
    # both result in open_issue using its default footer.
    assert not captured["kwargs"].get("footer_override")


# ─── Section ordering — end-to-end ────────────────────────────────────────


def test_section_order_is_canonical(monkeypatch):
    """Assert the canonical body section ordering:
       header → TL;DR → License → Why this candidate → Why this paper →
       Suggested experiment → (diff) → Why orchestrator opened Issue →
       What else Outrider considered"""
    captured = _capture(monkeypatch)
    run._open_downgrade_issue(
        Target(repo="example/repo", interest_id="iid"), _rec(),
        reason="downgrade reason heading",
        detail="downgrade detail body",
        tldr="at-a-glance",
        selection_note="picked because clean call site",
        selection_rejected=[{"arxiv_id": "X", "title": "Other", "reason": "no fit"}],
    )
    body = captured["body"]
    positions = {
        "tldr": body.index("## TL;DR"),
        "why_candidate": body.index("## Why this candidate"),
        "why_paper": body.index("## Why this paper is interesting for the team"),
        "suggested": body.index("## Suggested experiment"),
        "why_issue": body.index("## Why the orchestrator opened an Issue"),
        "what_else": body.index("## What else Outrider considered"),
    }
    ordered = [
        "tldr", "why_candidate", "why_paper", "suggested",
        "why_issue", "what_else",
    ]
    for prev, curr in zip(ordered, ordered[1:]):
        assert positions[prev] < positions[curr], (
            f"{prev} ({positions[prev]}) should come before "
            f"{curr} ({positions[curr]}) — order: "
            f"{sorted(positions.items(), key=lambda kv: kv[1])}"
        )
