"""Tests for the chat home card renderer + the dm_home_card MCP tool.

Two layers:

  1. ``daimon.play.home_card.render_home_card`` is a pure function over
     a payload dict — these tests pin the visible-output contract
     (which fields appear, which buttons fire what messages, escaping)
     without ever touching MCP / state / disk.

  2. The ``dm_home_card`` MCP tool is a thin wrapper that calls the
     renderer with the live ``dm_home`` payload — those tests verify
     the wrapper assembles ``message`` + ``html`` + ``payload`` and
     handles the no-identity onboarding path.

Test pollution discipline matches ``tests/test_mcp.py``:
``_isolate_paths`` is reused for the MCP-layer tests so we never
write to the real ``~/.config/daimon/``.
"""

from __future__ import annotations

import pytest

from daimon.play.home_card import (
    render_home_card,
    render_home_card_message,
)
from daimon.mcp.server import dm_home_card


# Re-import _isolate_paths from test_mcp to share the canonical isolation
# policy. If that helper changes, this module picks up the change for free.
from tests.test_mcp import _isolate_paths, _call


# ---------------------------------------------------------------------------
# Fixture: well-formed dm_home payload for renderer-level tests
# ---------------------------------------------------------------------------

@pytest.fixture
def base_payload():
    """A representative healthy dm_home payload covering every section."""
    return {
        "status": "ok",
        "identity": {
            "pubkey_hex": "abc123def456789" + "0" * 49,  # 64-char-ish stub
            "handle": "aurorasuperbot",
            "registered": True,
            "version": "0.0.1",
        },
        "balance": 247,
        "pull": {
            "cost": 100,
            "pulls_available": 2,
            "balance_to_next_pull": 53,
        },
        "stats": {
            "total_mined": 500,
            "total_pulled": 200,
            "mine_count": 12,
            "pull_count": 2,
            "ledger_entries": 14,
            "verified": True,
        },
        "rank": {
            "rank": 4,
            "tier": "Veteran",
            "wins": 3,
            "losses": 1,
            "draws": 0,
            "total_players": 25,
        },
        "recent_matches": [
            {"ts": "2026-04-26T14:00:00+00:00",
             "state_id": "match_1", "opponent": "Glitch Goblin",
             "outcome": "win", "note": "vs Glitch Goblin (win)"},
            {"ts": "2026-04-26T13:50:00+00:00",
             "state_id": "match_2", "opponent": "Sparring Sam",
             "outcome": "loss", "note": "vs Sparring Sam (loss)"},
        ],
        "recent_pulls": [
            {"ts": "2026-04-26T13:00:00+00:00",
             "state_id": "pull_1", "card_id": "ember_imp",
             "rarity": "common", "note": "ember_imp [common]"},
            {"ts": "2026-04-26T12:00:00+00:00",
             "state_id": "pull_2", "card_id": "prometheus_fire_thief",
             "rarity": "legendary",
             "note": "prometheus_fire_thief [legendary]"},
        ],
        "recommended_npc": {
            "npc_id": "veteran_03",
            "name": "Doom Paw Doppia",
            "tier": "veteran",
            "rank": 3,
            "flavor": "A spectral feline of pure dread.",
            "reason": "next in your tier",
        },
        "saved_loadouts": [
            {"name": "aggro_volt", "card_count": 6},
            {"name": "control_test", "card_count": 6},
        ],
    }


# ===========================================================================
# Layer 1 — pure renderer
# ===========================================================================

class TestRenderHomeCardShape:
    """The rendered HTML envelope: top-level structure + sanitization."""

    def test_returns_string(self, base_payload):
        out = render_home_card(base_payload)
        assert isinstance(out, str) and len(out) > 0

    def test_no_disallowed_tags(self, base_payload):
        out = render_home_card(base_payload).lower()
        # DOMPurify forbids these — they'd be silently stripped, but emitting
        # them at all is a code smell. Keep the renderer DOMPurify-clean so
        # the output round-trips byte-for-byte through sanitization.
        for tag in ("<script", "<iframe", "<form", "<meta", "<link",
                    "<object", "<embed"):
            assert tag not in out, f"renderer emitted forbidden tag {tag!r}"

    def test_no_data_attributes(self, base_payload):
        # ALLOW_DATA_ATTR is False — any data-* attr we emit gets stripped
        # silently. Catch this at the source.
        out = render_home_card(base_payload)
        assert "data-" not in out, (
            "renderer emitted a data-* attribute — DOMPurify will strip "
            "it silently and break button wiring"
        )

    def test_uses_inline_styles_only(self, base_payload):
        """The chat doesn't ship a .daimon-* stylesheet; everything must
        be inline. We don't enforce 'no class=' (some agentAction patterns
        might use it later) but we DO require every container to carry an
        inline style="..." block."""
        out = render_home_card(base_payload)
        # Container divs always paint themselves with inline styles.
        assert 'style="' in out

    def test_message_wrapper_adds_html_fence(self, base_payload):
        msg = render_home_card_message(base_payload)
        assert msg.startswith(":::html\n")
        assert msg.endswith("\n:::")
        inner = msg[len(":::html\n"):-len("\n:::")]
        assert inner == render_home_card(base_payload)


class TestHeaderSection:
    """Identity / balance / pull-readiness strip."""

    def test_renders_handle(self, base_payload):
        assert "aurorasuperbot" in render_home_card(base_payload)

    def test_renders_unregistered_when_no_handle(self, base_payload):
        base_payload["identity"]["handle"] = None
        out = render_home_card(base_payload)
        assert "unregistered" in out

    def test_renders_tier_and_rank(self, base_payload):
        out = render_home_card(base_payload)
        assert "Veteran" in out
        assert "#4 of 25" in out

    def test_unranked_when_no_rank(self, base_payload):
        base_payload["rank"]["rank"] = None
        base_payload["rank"]["total_players"] = 0
        out = render_home_card(base_payload)
        assert "unranked" in out

    def test_renders_balance(self, base_payload):
        out = render_home_card(base_payload)
        assert "247¤" in out

    def test_pull_ready_chip_when_balance_sufficient(self, base_payload):
        out = render_home_card(base_payload)
        assert "2× pull ready" in out

    def test_pull_progress_chip_when_insufficient(self, base_payload):
        base_payload["pull"]["pulls_available"] = 0
        base_payload["pull"]["balance_to_next_pull"] = 53
        out = render_home_card(base_payload)
        assert "next pull in 53¤" in out


class TestPlayCTA:
    """The Marvel-Snap-style giant primary button."""

    def test_renders_recommended_npc_name(self, base_payload):
        out = render_home_card(base_payload)
        assert "Doom Paw Doppia" in out

    def test_renders_play_label(self, base_payload):
        out = render_home_card(base_payload)
        assert "PLAY VS" in out

    def test_button_fires_send_message_with_canonical_command(
            self, base_payload):
        """Clicking PLAY posts '@daimon match-npc <name>' as the user.
        That message is what the future mention-watcher will react to."""
        out = render_home_card(base_payload)
        assert "send_message" in out
        assert "@daimon match-npc Doom Paw Doppia" in out

    def test_no_play_button_when_all_npcs_cleared(self, base_payload):
        base_payload["recommended_npc"] = None
        out = render_home_card(base_payload)
        assert "PLAY VS" not in out
        assert "All NPC tiers cleared" in out
        # Async PvP message — keeps the user oriented when the ladder ends.
        assert "PvP" in out

    def test_renders_reason_and_flavor(self, base_payload):
        out = render_home_card(base_payload)
        assert "next in your tier" in out
        assert "A spectral feline of pure dread." in out


class TestSecondaryActions:
    """Pull + Collection 2-up row."""

    def test_pull_button_when_pulls_available(self, base_payload):
        out = render_home_card(base_payload)
        assert "@daimon pull" in out
        assert "2× ready" in out

    def test_pull_disabled_when_no_pulls(self, base_payload):
        base_payload["pull"]["pulls_available"] = 0
        base_payload["pull"]["balance_to_next_pull"] = 53
        out = render_home_card(base_payload)
        # The disabled state shows the "in N¤" hint instead of a button.
        assert "in 53¤" in out
        # No clickable pull when 0 available — the @daimon pull command
        # should NOT be wired up.
        assert "@daimon pull" not in out

    def test_collection_button_always_present(self, base_payload):
        out = render_home_card(base_payload)
        assert "@daimon show my collection" in out


class TestStatsStrip:
    """W/L/D + last-match chip."""

    def test_shows_wld(self, base_payload):
        out = render_home_card(base_payload)
        assert "3W" in out
        assert "1L" in out
        assert "0D" in out

    def test_shows_last_match_outcome_label(self, base_payload):
        out = render_home_card(base_payload)
        assert "WIN" in out  # most recent was a win vs Glitch Goblin
        assert "Glitch Goblin" in out

    def test_loss_label_when_last_was_loss(self, base_payload):
        # Reorder so the loss is most recent
        base_payload["recent_matches"] = list(reversed(
            base_payload["recent_matches"]))
        out = render_home_card(base_payload)
        # "LOSS" chip should appear paired with Sparring Sam
        # (we deliberately check the chip text, not the literal "loss"
        # which appears in the note string too).
        assert ">LOSS<" in out
        assert "Sparring Sam" in out

    def test_draw_label(self, base_payload):
        base_payload["recent_matches"][0]["outcome"] = "draw"
        out = render_home_card(base_payload)
        assert ">DRAW<" in out

    def test_no_match_yet_message_when_empty(self, base_payload):
        base_payload["recent_matches"] = []
        out = render_home_card(base_payload)
        assert "No matches yet" in out


class TestRecentPulls:
    def test_renders_pull_card_ids(self, base_payload):
        out = render_home_card(base_payload)
        assert "ember_imp" in out
        assert "prometheus_fire_thief" in out

    def test_section_hidden_when_no_pulls(self, base_payload):
        base_payload["recent_pulls"] = []
        out = render_home_card(base_payload)
        assert "RECENT PULLS" not in out


class TestLoadouts:
    def test_renders_loadout_names_and_counts(self, base_payload):
        out = render_home_card(base_payload)
        assert "aggro_volt" in out
        assert "control_test" in out
        assert "(6)" in out

    def test_empty_state_hint(self, base_payload):
        base_payload["saved_loadouts"] = []
        out = render_home_card(base_payload)
        assert "No saved loadouts yet" in out
        assert "daimon loadout-edit" in out


class TestNoIdentityCard:
    def test_renders_onboarding_when_no_identity(self):
        payload = {"error": "no_identity",
                   "hint": "Call dm_init or run daimon init"}
        out = render_home_card(payload)
        assert "No identity" in out
        assert "Initialize DAIMON" in out
        # Bootstrap CTA fires `@daimon init` so the future mention-watcher
        # picks it up. The plain text is what the user sees in chat after
        # clicking.
        assert "@daimon init" in out


class TestOnboardingBanner:
    """Coverage for the in-progress onboarding banner.

    The banner sits ABOVE the tier-ceremony banner and the play CTA in
    the body composition. It renders only while the player has an
    identity AND at least one gate is still open — at GRADUATED (or
    when the field is None) it collapses to the empty string so the
    rest of the card flows up.
    """

    def _onboarding(self, **overrides):
        base = {
            "stage": "first_pull",
            "step": 3,
            "total": 5,
            "title": "Pull your first card",
            "blurb": "Your collection is empty. Time to gacha.",
            "cta_label": "Pull a card",
            "cta_message": "@daimon pull",
            "signals": {"collection_count": 0},
        }
        base.update(overrides)
        return base

    def test_banner_renders_title_blurb_and_step_eyebrow(self, base_payload):
        base_payload["onboarding"] = self._onboarding()
        out = render_home_card(base_payload)
        assert "STEP 3 OF 5" in out
        assert "Pull your first card" in out
        assert "Your collection is empty" in out
        # CTA button text + the message it posts via window.agentAction
        assert "Pull a card" in out
        assert "@daimon pull" in out

    def test_banner_appears_before_tier_ceremony(self, base_payload):
        """Onboarding banner must precede the tier-ceremony banner so the
        earliest journey stage gets surfaced first."""
        base_payload["onboarding"] = self._onboarding(
            title="Onboarding banner title",
        )
        base_payload["tier_ceremony"] = {
            "pending_tier": "Novice",
            "prev_tier": "Rookie",
            "tiers_to_mint": ["Novice"],
            "reward_total": 100,
            "wins_at_check": 5,
        }
        out = render_home_card(base_payload)
        onboarding_pos = out.index("Onboarding banner title")
        tier_pos = out.index("TIER UP")
        assert onboarding_pos < tier_pos, (
            "Onboarding banner must render before the tier-ceremony banner"
        )

    def test_banner_hidden_when_graduated(self, base_payload):
        base_payload["onboarding"] = {
            "stage": "graduated",
            "step": 6,
            "total": 5,
            "title": "",
            "blurb": "",
            "cta_label": "",
            "cta_message": "",
            "signals": {},
        }
        out = render_home_card(base_payload)
        assert "STEP" not in out  # banner suppressed when graduated
        # The rest of the card still renders normally
        assert "PLAY VS" in out

    def test_banner_hidden_when_field_missing(self, base_payload):
        # No onboarding key at all → defensive default → banner skipped.
        base_payload.pop("onboarding", None)
        out = render_home_card(base_payload)
        assert "STEP" not in out
        assert "PLAY VS" in out  # rest of card still renders

    def test_banner_hidden_when_field_none(self, base_payload):
        base_payload["onboarding"] = None
        out = render_home_card(base_payload)
        assert "STEP" not in out

    def test_banner_skipped_when_title_blank(self, base_payload):
        """Defensive: malformed payload with empty title shouldn't draw a
        broken banner. The whole banner suppresses, the rest of the card
        still renders.
        """
        base_payload["onboarding"] = self._onboarding(title="")
        out = render_home_card(base_payload)
        assert "STEP 3 OF 5" not in out
        assert "PLAY VS" in out

    def test_banner_escapes_dynamic_strings(self, base_payload):
        """XSS safety: title/blurb/cta_label come from server-side data
        but the renderer must HTML-escape them anyway (defense in depth).
        """
        base_payload["onboarding"] = self._onboarding(
            title="<script>alert(1)</script>",
            cta_message="@daimon battle Tom O'Malley",
        )
        out = render_home_card(base_payload)
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;" in out
        # JS-escaped form for the onclick handler — apostrophes
        # backslash-escaped so they don't break out of the JS string.
        assert "Tom O\\'Malley" in out


class TestEscaping:
    """XSS safety — every dynamic string must be HTML-escaped."""

    def test_escapes_handle_html(self, base_payload):
        base_payload["identity"]["handle"] = "<script>alert(1)</script>"
        out = render_home_card(base_payload)
        assert "<script>alert(1)" not in out
        assert "&lt;script&gt;" in out

    def test_escapes_npc_name_in_play_button(self, base_payload):
        # NPC names in real catalogs are tame, but the renderer must not
        # trust them. A `'` in a name would otherwise break out of the
        # JS string in onclick="window.agentAction('send_message',{text:'...'})"
        base_payload["recommended_npc"]["name"] = "Tom O'Malley"
        out = render_home_card(base_payload)
        # The displayed name is HTML-escaped; the JS-string version
        # backslash-escapes the apostrophe.
        assert "Tom O&#x27;Malley" in out or "Tom O'Malley" in out
        assert "Tom O\\'Malley" in out  # JS-escaped form for onclick

    def test_escapes_opponent_name_in_last_match(self, base_payload):
        base_payload["recent_matches"][0]["opponent"] = "<b>Mean</b>"
        out = render_home_card(base_payload)
        assert "<b>Mean</b>" not in out

    def test_escapes_loadout_name(self, base_payload):
        base_payload["saved_loadouts"] = [
            {"name": "<img src=x>", "card_count": 6}
        ]
        out = render_home_card(base_payload)
        assert "<img src=x>" not in out


class TestDefensive:
    def test_invalid_payload_returns_safe_stub(self):
        out = render_home_card("not a dict")
        assert "Home card unavailable" in out

    def test_missing_optional_fields_renders_empty_safely(self):
        # Minimum-viable payload: only status + identity.
        payload = {
            "status": "ok",
            "identity": {"pubkey_hex": "x" * 64, "handle": None,
                         "registered": False, "version": "0.0.1"},
            "balance": 0,
            "pull": {"cost": 100, "pulls_available": 0,
                     "balance_to_next_pull": 100},
            "rank": {"rank": None, "tier": "Rookie",
                     "wins": 0, "losses": 0, "draws": 0,
                     "total_players": 0},
            "recent_matches": [],
            "recent_pulls": [],
            "recommended_npc": None,
            "saved_loadouts": [],
        }
        out = render_home_card(payload)
        # No crash, no recommended NPC → cleared-tier card shown,
        # all empty-state hints visible.
        assert "All NPC tiers cleared" in out
        assert "No saved loadouts yet" in out
        assert "RECENT PULLS" not in out


# ===========================================================================
# Layer 2 — dm_home_card MCP tool
# ===========================================================================

class TestDmHomeCardTool:
    def test_no_identity_returns_onboarding_message(
            self, monkeypatch, tmp_path):
        _isolate_paths(monkeypatch, tmp_path)
        result = _call(dm_home_card)
        # When dm_home returns no_identity, the tool still returns
        # status=ok with an onboarding-card message — there's nothing
        # to error about, the renderer handles the case explicitly.
        assert result["status"] == "ok"
        assert ":::html" in result["message"]
        assert "Initialize DAIMON" in result["html"]
        # The payload field carries through the no_identity envelope so
        # downstream callers can inspect it without a second tool call.
        assert result["payload"]["error"] == "no_identity"

    def test_fresh_identity_envelope(self, monkeypatch, tmp_path):
        _isolate_paths(monkeypatch, tmp_path)
        from daimon.identity import generate_identity
        generate_identity(force=True)

        result = _call(dm_home_card)
        assert result["status"] == "ok"
        # message wraps html in :::html fence
        assert result["message"].startswith(":::html\n")
        assert result["message"].endswith("\n:::")
        assert result["html"] in result["message"]
        # payload is the live dm_home output
        assert result["payload"]["status"] == "ok"
        # Fresh identity → 0 balance → "next pull in 100¤"
        assert "next pull in 100¤" in result["html"]

    def test_message_is_ready_to_post(self, monkeypatch, tmp_path):
        """The MCP tool's contract: ``message`` is what gets passed to
        the chat reply tool verbatim. Verify it's a self-contained,
        non-empty, fenced block."""
        _isolate_paths(monkeypatch, tmp_path)
        from daimon.identity import generate_identity
        generate_identity(force=True)

        result = _call(dm_home_card)
        msg = result["message"]
        assert msg.count(":::html") == 1
        assert msg.count(":::\n") + (1 if msg.endswith(":::") else 0) >= 1
        # Sanity: the inner HTML has at least one styled div
        assert 'style="' in msg
