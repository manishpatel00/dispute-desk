# DESIGN.md - Dispute Desk Design System

> Built following the **Together AI / VoltAgent** design system and **Egonex-AI / addyosmani** agent engineering specifications.

---

## 1. Overview & System Philosophy

Dispute Desk is a mission-critical billing dispute investigation platform: deterministic recalculation, agentic interpretation, and human approval. The brand and visual language project an uncompromising posture of precision, transparency, and high-performance infrastructure:

- **Near-black hero on top (`#010120`)**: Anchor of seriousness and operational authority.
- **Three-color brand gradient ribbon (`#fc4c02` → `#ef2cc1` → `#bdbbff`)**: The signature brand chrome representing data transformation, from raw metrics to resolved consensus.
- **Crisp white canvas (`#ffffff`)**: Purpose-built for dense financial data, calculation grids, and auditable findings.
- **Two-face typographic contrast**: Custom geometric display sans for sentence-case headlines (with negative tracking) paired with all-caps monospace (`JetBrains Mono` / `Geist Mono`) for every eyebrow, button, table header, and status badge.
- **Deterministic 4px radius (`{rounded.sm}`)**: Clean hairline borders (`#ebebeb` / `#26263a`), rejecting gratuitous floating drop shadows.

```
┌────────────────────────────────────────────────────────────────────────┐
│  HERO BAND (Canvas Dark #010120)                                       │
│  All-caps mono eyebrow · Sentence-case headline · Gradient ribbon SVG  │
│  Black & mint pill actions · Evidence & system health telemetry        │
├────────────────────────────────────────────────────────────────────────┤
│  TECHNICAL CANVAS (Canvas #ffffff)                                     │
│  Cases sidebar · Recalculation data table (hairline #ebebeb header)    │
│  Agent findings cards · Credit resolution with deterministic cap       │
│  Evidence injection · Audit log trail                                  │
├────────────────────────────────────────────────────────────────────────┤
│  TERMINAL SIGN-OFF                                                     │
│  Massive dispute.desk wordmark in #ebebeb stencil tint                 │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Architecture: Deterministic Facts, LLM Meaning

The system separates **deterministic calculation** from **AI semantic reasoning** (Egonex-AI pattern):

```
┌────────────────────────────────────────────────────────────────────────┐
│                          DETERMINISTIC ENGINE                          │
│  app/engine.py: Decimal arithmetic (no floating point), usage cleaning │
│  deduplication, out-of-period filtering, tiered pricing calculations    │
│  app/store.py: SQLite WAL mode, BEGIN IMMEDIATE, idempotency check,     │
│  credit capping, stale calculation tracking                            │
├────────────────────────────────────────────────────────────────────────┤
│                                   │                                    │
│                                   ▼                                    │
│                    AGENTIC INTERPRETATION GRAPH                        │
│  app/agent.py: gather → calculate → interpret (Claude / Anthropic API) │
│  → verify citations against verified evidence hashes                   │
│  Automatic fallback to rule-based engine on API absence or failure     │
├────────────────────────────────────────────────────────────────────────┤
│                                   │                                    │
│                                   ▼                                    │
│                         HUMAN-IN-THE-LOOP UI                           │
│  Reviewer approves / edits / rejects citations and findings            │
│  Credit approval strictly bounded by recalculated delta                │
└────────────────────────────────────────────────────────────────────────┘
```

### Immutable Invariants:
1. **Zero LLM Math**: LLMs never compute sums, tier cutoffs, or credit caps.
2. **Citation Verification**: Findings without real line, rule, or event IDs are discarded by `verify()`.
3. **Evidence Invalidation**: Any new evidence patch flags prior calculations and findings as `stale`.
4. **Idempotent Settlement**: Repeated credit clicks cannot double-credit due to cryptographic idempotency keys.

---

## 3. Color Tokens

### Brand & Accents
| Token | Hex | Role |
|---|---|---|
| `{colors.primary}` | `#000000` | The primary CTA background. Black pill for conversion targets. |
| `{colors.accent-orange}` | `#fc4c02` | First stop of the three-color brand ribbon. |
| `{colors.accent-magenta}` | `#ef2cc1` | Center stop of the brand ribbon. |
| `{colors.accent-periwinkle}` | `#bdbbff` | Third stop of brand ribbon; tint for telemetry highlights. |
| `{colors.accent-mint}` | `#c8f6f9` | Pastel cyan for hero secondary actions and stat tiles. |

### Surfaces & Lines
| Token | Hex | Role |
|---|---|---|
| `{colors.canvas}` | `#ffffff` | Primary product background, data tables, cards. |
| `{colors.canvas-dark}` | `#010120` | Dark hero band and research/audit surfaces. |
| `{colors.hairline}` | `#ebebeb` | 1px dividers on light surfaces, table headers, borders. |
| `{colors.hairline-dark}` | `#26263a` | 1px dividers and badge borders on dark surfaces. |
| `{colors.surface-dark-soft}` | `#171b26` | Lighter dark fill inside hero containers and dark cards. |

### Text & Telemetry
| Token | Hex | Role |
|---|---|---|
| `{colors.ink}` | `#000000` | Headings, primary body on light canvas. |
| `{colors.body}` | `#666666` | Secondary labels, captions, metadata. |
| `{colors.on-dark}` | `#ffffff` | All text on `{colors.canvas-dark}`. |
| `{colors.bad}` | `#b3261e` | Overbilling discrepancies, calculation mismatch flags. |
| `{colors.ok}` | `#1b6b4a` | Reconciled lines, accepted status, approved credits. |
| `{colors.warn}` | `#8a5a00` | Contract ambiguity flags, pending human judgment. |

---

## 4. Typography Scale

Following the Together AI typographic rules:
- **Display Sans**: `Public Sans` / `Inter` (weight 500, tight negative tracking `-0.02em` to `-0.04em`). Headlines are strictly **sentence-case**.
- **Monospace Face**: `JetBrains Mono` / `SF Mono` / `Geist Mono` (weight 500, positive tracking `+0.04em` to `+0.08em`). Strictly **uppercase** for eyebrows, buttons, badges, table headers.

| Token | Size | Weight | Tracking | Purpose |
|---|---|---|---|---|
| `{typography.display-xxl}` | 52px | 500 | `-0.04em` | Hero main headline (sentence case). |
| `{typography.display-xl}` | 32px | 500 | `-0.025em` | Major panel headings. |
| `{typography.display-lg}` | 22px | 500 | `-0.015em` | Card titles and section headers. |
| `{typography.body-md}` | 15px | 400 | `-0.01em` | Standard descriptive copy, dispute text. |
| `{typography.mono-caps-eyebrow}` | 11px | 500 | `+0.06em` | Section tags, table headers, telemetry labels. |
| `{typography.mono-caps-button}` | 12px | 500 | `+0.08em` | Primary buttons, action triggers. |
| `{typography.mono-tabular}` | 13px | 400 | `0` | Currency, quantities, IDs, citation hashes. |

---

## 5. Components & Layout Rules

### Buttons
- **`button-primary`**: Black `#000000` solid pill with white uppercase mono label. `{rounded.sm}` (4px).
- **`button-mint`**: Mint `#c8f6f9` solid pill with black ink uppercase mono label.
- **`button-ghost`**: Transparent fill, 1px solid `{colors.hairline}`, uppercase mono text.
- **`button:disabled`**: 50% opacity, inline loading spinner, `cursor: wait`.

### Data Tables
- **Header**: Background `{colors.hairline}` (`#ebebeb`), text `{typography.mono-caps-eyebrow}`, uppercase.
- **Rows**: Alternating subtle hover, 1px solid `{colors.hairline}` bottom separator. Numeric cells aligned right with `font-variant-numeric: tabular-nums`.

### Findings Cards
- Hairline border, `{rounded.sm}` (4px), color-coded left border:
  - Red for `calculation_error`
  - Amber for `contract_ambiguity`
- Citation pills listing exact evidence references (`INV-`, `L1`, `U3`, `R1`).
- Direct action controls: Accept, Edit, Reject.

### Terminal Wordmark Banner
- Massive fluid wordmark `dispute.desk` at the bottom of the page in `{colors.hairline}` (`#ebebeb`), creating a signature infrastructure sign-off.

---

## 6. Do's and Don'ts

### Do
- Reserve `{colors.primary}` (`#000000`) for primary action targets.
- Set every section eyebrow and button label in uppercase monospace with positive tracking.
- Apply the three-stop brand gradient (`#fc4c02` → `#ef2cc1` → `#bdbbff`) at hero scale as the primary decorative chrome.
- Maintain `{rounded.sm}` 4px as canonical across all cards, buttons, badges, and inputs.
- Keep numbers and financial calculations strictly deterministic with `Decimal` precision.

### Don't
- Never let an LLM perform mathematical arithmetic or set credit caps.
- Never write paragraphs in all-caps monospace. Monospace is strictly for metadata, labels, and citations.
- Never use heavy drop shadows on light surfaces - maintain clean hairlines.
- Never omit citations when generating or validating findings.
