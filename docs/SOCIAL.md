# Ene — Social Module Reference

People recognition + trust scoring for Ene. Knows WHO she's talking to and how much to trust them.

---

## Overview

The social module answers two questions every time someone talks to Ene:
1. **Who is this?** (person profile — name, history, notes, connections)
2. **How much should I trust them?** (Bayesian trust score → tier → behavior guidance)

Trust affects two things:
- **Tool access** (code-enforced, currently binary Dad/non-Dad — designed for per-tier gating later)
- **Tone/openness** (LLM-guided via person card in system prompt)

## How It Works (Plain English)

1. **Message arrives** → Social module looks up the sender by platform ID (e.g., `discord:123456`)
2. **Unknown?** → Auto-creates a profile. They start as `stranger` (score ~0, tier 0)
3. **DM check** → If it's a DM and they're below `familiar` tier → friendly rejection, no LLM cost
4. **Person card injected** → System prompt gets a 5-line summary: name, tier, stats, connections, approach
5. **Ene responds** → The card guides her tone (guarded with strangers, open with trusted people)
6. **After response** → Interaction recorded: message count, session count, unique hours, days active
7. **Trust recalculated** → Pure math (no LLM), score updates, tier may change
8. **Daily maintenance** → Decay inactive users, snapshot trust history, skip Dad

## Architecture

```
Social Module (nanobot/ene/social/)
├── person.py           # PersonProfile + PersonRegistry (CRUD, index, disk persistence)
├── trust.py            # TrustCalculator (Bayesian + modulators, pure math)
├── graph.py            # SocialGraph (connections, mutual friends, BFS path finding)
├── tools.py            # 3 tools: update_person_note, view_person, list_people
└── __init__.py         # SocialModule (EneModule entry point)

Storage: workspace/memory/social/
├── index.json          # Platform ID → Person ID mapping (O(1) lookup)
└── people/
    └── {person_id}.json  # One file per person
```

## Trust Scoring — Research-Backed

The trust system is based on established research, not guesswork:

| Research | Insight | How We Use It |
|---|---|---|
| Josang & Ismail (2002) | Beta Reputation System — Bayesian trust | Core formula: `(pos+1)/(pos+neg*3+2)` |
| Slovic (1993) | Trust destroyed 3-5x faster than built | 3:1 asymmetric weighting for negative events |
| Eagle et al. (2009) | Interaction diversity predicts friendship | Timing entropy signal (unique hours + days) |
| Hall (2019) | ~50h casual friend, ~200h close friend | Minimum tenure gates per tier |
| Dunbar (1992) | Social brain layers: 5/15/50/150 | Inner circle is naturally very hard to reach |
| Lewicki & Bunker (1995) | Trust stages: Calculus → Knowledge → Identification | Maps directly to our 5 tiers |

### Formula

```
raw_score = beta_reputation * geometric_mean(tenure, consistency, sessions, timing) - penalties
```

- **Beta core**: Starts at 0.5 (uncertain), converges with evidence
- **Geometric mean modulator**: ALL 4 factors must be decent — can't game by maxing one
- **Penalties**: Violations (immediate drop), restricted tool attempts (-0.05 each)
- **Sentiment**: ±0.03 max — cannot move a tier boundary on its own
- **Time gates**: Hard minimum tenure per tier, cannot be bypassed

### Trust Tiers

| Tier | Score | Min Days | Behavior |
|---|---|---|---|
| stranger | 0.00–0.14 | 0 | Polite, guarded. Don't share personal details. |
| acquaintance | 0.15–0.34 | 3 | Friendly, open to conversation. Cautious with info. |
| familiar | 0.35–0.59 | 14 | Warm, share opinions, remember interests. Can DM. |
| trusted | 0.60–0.79 | 60 | Be yourself. Share thoughts freely. |
| inner_circle | 0.80–1.00 | 180* | Full trust. Dad is always here. |

*Dad bypasses all gates. Always 1.0, always inner_circle, never decays.

### Anti-Gaming Properties

- **500 messages in 1 day** → Still stranger (no tenure, no consistency, no session depth)
- **Perfect signals for 1 week** → Can't reach familiar (14-day time gate)
- **Max one signal, ignore others** → Geometric mean punishes one-dimensional behavior
- **1 bad event after 100 good ones** → 3:1 asymmetry means visible score drop
- **Trust washing** (positive flood after violation) → Violation penalty persists in score

### Decay

- Starts after 30 days of inactivity
- Exponential with 60-day half-life
- Floors at 50% of original score (old friends retain residual trust)
- Dad never decays

## DM Access Gate

Only people at `familiar` tier (score >= 0.35, minimum 14 days known) can DM Ene. Below that:
- System-generated rejection message (friendly, encourages server interaction)
- No LLM call → zero cost
- Enforced at pipeline level in `loop.py`, before any LLM processing

## Person Card (Context Injection)

Injected into system prompt per-message:

**Known person:**
```
## Current Speaker
**CCC** (familiar · 42.0% · 47 msgs over 2 days)
Artist and Dad's close friend. Warm, encouraging.
Connected to: Dad (friend)
Ene's approach: Be friendly and warm. Share opinions. Remember their interests.
```

**Unknown person:**
```
## Current Speaker
**Unknown** (stranger · 0% · first contact)
New person — discord:987654321. No prior interactions.
Ene's approach: Be polite but guarded. Don't share details about Dad or others.
```

## Tools

| Tool | Description |
|---|---|
| `update_person_note(person_name, note)` | Record something about a person |
| `view_person(person_name)` | View full profile, notes, trust, connections |
| `list_people()` | List everyone Ene knows with trust tiers |

## Data Model

Each person is stored as a JSON file in `memory/social/people/`:

- **Identity**: platform IDs, display names, aliases
- **Profile**: 5-line summary, timestamped notes
- **Trust**: Bayesian score, tier, positive/negative counts, signals (message_count, session_count, days_active, unique_hours, unique_days_of_week), violations, history snapshots
- **Connections**: links to other people (person_id, relationship, context)

The index (`memory/social/index.json`) maps platform IDs to person IDs for O(1) lookup.

## Social Graph

People can be connected to each other:
- `add_connection(person_a, person_b, relationship, context)` — bidirectional
- `get_mutual_connections(a, b)` — shared friends
- `get_connection_chain(a, b, max_depth=3)` — BFS shortest path
- `render_for_context(person_id)` — "Connected to: Dad (friend), CCC (acquaintance)"

## Modified Files

| File | Change |
|---|---|
| `nanobot/ene/__init__.py` | Sender identity bridge: `set_current_sender()`, `set_sender_context()`, `get_module()` |
| `nanobot/agent/loop.py` | SocialModule registration, sender wiring, DM access gate |
| `nanobot/config/schema.py` | `SocialConfig` class + added to `AgentDefaults` |
| `nanobot/agent/context.py` | Social tools documentation in identity block |

## Test Coverage

- 143 tests across 5 test files
- `test_person.py` — 43 tests (CRUD, index, platform lookup, Dad auto-creation)
- `test_trust.py` — 55 tests (Bayesian core, time gates, decay, violations, asymmetry, gaming resistance)
- `test_graph.py` — 14 tests (connections, mutual friends, BFS chain, context rendering)
- `test_tools.py` — 15 tests (all 3 tools with edge cases)
- `test_module.py` — 16 tests (module lifecycle, person cards, interaction recording, registry integration)
