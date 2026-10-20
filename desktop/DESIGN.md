# Saarathi Desktop design system

The single source of truth for how the app looks, moves and gives feedback. Built from the UI/UX Pro Max skill
(v2.13.0) on 2026-10-20 and the owner's brief: as minimal as Apple, as smooth as Nike, with rich, meaningful motion.

## Direction

| Decision | Choice | Basis |
|---|---|---|
| Style | Minimalism & Swiss, with AI-native touches (working indicators, streaming reveals, context cards) | Skill design system (`--design-system`, variance 3, motion 8, density 6); product matches "Developer Tool / IDE" and "AI platform" |
| Mode | Dark first; light and "follow system" available | Skill: "avoid light mode default" for developer tools; Dark Mode (OLED) style |
| Accent | AI indigo by default (`#8b8cf8` dark, `#4f46e5` light); six alternatives in Settings | Skill: AI-native palette (#6366F1 family) |
| Type | System UI (SF Pro on macOS, Segoe UI Variable on Windows) for the interface; JetBrains Mono, bundled locally, for code, ids and logs | Owner's Apple brief; skill's developer pairing for monospace. The skill's IBM Plex Sans body suggestion was not used, deliberately |
| Icons | Lucide, one stroke style, decorative icons hidden from assistive tech | Skill pro-rules: vector icons, stroke consistency |

## Tokens (`src/styles.css`)

- **Surfaces**: `bg` → `panel` → `raised` → `elevated`; borders `line` and `line-strong`. Light theme redefines every
  token; nothing is inverted.
- **Text**: `ink` and `muted` meet 7:1 on `bg`; `faint` meets 4.5:1. Semantic `ok`, `warn`, `err` meet 4.5:1 in both
  themes and always come with an icon or text (never colour alone).
- **Radii**: 16 px cards, 10 px controls, full pills. **Elevation**: `.elev-1` to `.elev-3`, one shadow language.
- **Numbers**: `.tabular` (tabular figures) wherever values change, so neighbours never shift.

## Motion (`src/components/primitives.tsx`)

| Token | Use | Value |
|---|---|---|
| `SPRING` | Presses, sliding selection indicators, toasts, the palette | spring 520 / 38 |
| `SOFT` | Content arriving: pages, cards, list items | spring 260 / 30 |
| `EXIT` | Anything leaving; faster than it arrived and accelerating away | 160 ms, ease-in |
| `STAGGER` | Between list items | 40 ms |

Rules applied from the skill (§7): springs over fixed curves; exits faster than entrances; transform and opacity only
(the execution panel's width animation is the one deliberate exception, so the conversation reflows smoothly); every
animation explains a change (a view replaced, an item added, a selection moved); animations never block input; reduced
motion (system setting or Settings → Motion) turns them all off, including CSS keyframes.

## Loading and feedback (`src/components/feedback.tsx`)

| Situation | Treatment |
|---|---|
| App start | `BootScreen`: the mark animates (agents appear, paths converge into the arrow) while the engine starts |
| Lists, cards, inspector, timeline loading | Shimmer skeletons in the final layout (no layout jump), `aria-busy` |
| The engine driving a run | `ThinkingDots` working row with the real phase; run bar progress from task counts, indeterminate sweep otherwise |
| Plan limits being read | Skeleton meters plus the working pulse ("Antigravity takes about ten seconds") |
| Buttons doing work | `Button loading`: spinner over the label, width kept |
| Numbers | `AnimatedNumber` counts up once, tabular figures |
| Success and errors | Toasts: polite live region, never take focus, auto-dismiss (4 s, errors 7 s), errors carry the recovery hint |
| A render error | `ErrorBoundary`: explains that runs are safe in the engine, offers reload |

Indicators reflect real state only: nothing pretends to progress.

## Navigation

Sidebar (sessions, Usage, Permissions, Settings) with a sliding highlight; ⌘K command palette (sessions, pages,
actions); ⌘N new session; ⌘, Settings; Esc closes the inspector or palette.

## Pre-delivery checklist (skill)

- [x] No emoji icons; Lucide throughout; decorative icons `aria-hidden`
- [x] Pointer cursor and eased hover/press states on every control
- [x] Contrast: 4.5:1 minimum for text in both themes, checked separately
- [x] Visible focus ring (`:focus-visible`), keyboard paths for palette, tabs and dialogs
- [x] `prefers-reduced-motion` and an in-app Motion setting
- [x] Loading feedback matched to the wait; no layout shift from async content
- [ ] Windows and Linux visual pass (CI builds only so far)
