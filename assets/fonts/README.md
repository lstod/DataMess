# Vendored fonts

One font, committed rather than resolved from the system, for the reason the plan gives for
rejecting `pdf2image`: anything the corpus needs that is not in the repo or in a wheel is an
install failure waiting for somebody else's machine.

macOS ships `Bradley Hand`, `Chalkduster` and `Comic Sans MS`, any of which would render mess
case 8. None of them exist on a Linux CI runner, and a corpus that renders differently on two
machines has no determinism contract at all — `corpus/pages.sha256` would go red for a reason
that has nothing to do with the content.

| File | Used by | Licence |
|---|---|---|
| `Kalam-Regular.ttf` | `scripts/degrade.py`, mess case 8's handwritten annotation | SIL Open Font License 1.1 — `Kalam-OFL.txt` |

Kalam is by the Indian Type Foundry, from Google Fonts. It was chosen over the alternatives
because its strokes stay legible after the step-3 degradation pass: case 8 asks a reader to
notice that a handwritten note contradicts the printed terms, and a note nobody can read tests
nothing. The OFL permits redistribution provided the licence travels with the font, which is
why `Kalam-OFL.txt` sits beside it.
