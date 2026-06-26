"""
src/eval/classify_scorecard.py
Classify-accuracy scorecard (issue #4): measure the Finder classifier's
persona + focus-area precision/recall/F1 against a labeled set.

Classifier-agnostic by design. ``score_classifier`` takes a ``classify_fn``, so:
  - the metric logic is unit-tested with a deterministic fake (no API key, runs
    in CI under the coverage gate), and
  - a live run injects the real Haiku-backed ``finder._classify_contact`` to
    produce the baseline scorecard.

Closes the gap the Finder audit named (FINDER_AUDIT D3): classify was tested for
shape, never for accuracy. The live entrypoint is marked ``# pragma: no cover``
because it hits the network — only the pure scoring logic is covered.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from src.core.schemas import FocusArea, Persona

# ponytail: file-relative load — works for editable/source installs (our case).
# Add package-data config if this ever ships in a built wheel (v0.10.5 packaging).
_LABELED_SET_PATH = Path(__file__).with_name("classify_labeled_set.json")


class LabeledContact(BaseModel):
    """One ground-truth contact: inputs the classifier sees + the gold labels."""

    full_name: str
    title: str
    company_slug: str
    snippet: str | None = None
    expected_persona: Persona
    expected_focus_area: FocusArea
    note: str | None = None  # why this label / what edge case it stresses


# A classifier under test: given a labeled contact's inputs, predict its labels.
ClassifyFn = Callable[[LabeledContact], "tuple[Persona, FocusArea]"]


@dataclass
class ClassMetrics:
    """Precision/recall/F1 for one class label within a dimension."""

    label: str
    support: int  # gold items with this label
    predicted: int  # predictions of this label
    correct: int  # true positives

    @property
    def precision(self) -> float:
        return self.correct / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class DimensionReport:
    """Metrics for one classification dimension (persona or focus_area)."""

    name: str
    n: int
    accuracy: float
    per_class: dict[str, ClassMetrics]
    confusion: dict[str, dict[str, int]]  # expected -> predicted -> count

    @property
    def macro_f1(self) -> float:
        # Average F1 over classes that appear as either gold or prediction.
        active = [m for m in self.per_class.values() if m.support or m.predicted]
        return sum(m.f1 for m in active) / len(active) if active else 0.0


@dataclass
class Scorecard:
    persona: DimensionReport
    focus_area: DimensionReport
    n: int
    errors: list[tuple[LabeledContact, Persona, FocusArea]]  # (gold, pred_p, pred_f)


def load_labeled_set(path: str | Path | None = None) -> list[LabeledContact]:
    """Load and validate the labeled set (enum labels checked on load)."""
    p = Path(path) if path else _LABELED_SET_PATH
    raw = json.loads(p.read_text())
    return [LabeledContact(**row) for row in raw]


def _score_dimension(
    name: str, golds: list[str], preds: list[str], labels: list[str]
) -> DimensionReport:
    confusion: dict[str, dict[str, int]] = {g: defaultdict(int) for g in labels}
    support: dict[str, int] = defaultdict(int)
    predicted: dict[str, int] = defaultdict(int)
    correct: dict[str, int] = defaultdict(int)
    n_correct = 0
    for gold, pred in zip(golds, preds, strict=True):
        confusion[gold][pred] += 1
        support[gold] += 1
        predicted[pred] += 1
        if gold == pred:
            correct[gold] += 1
            n_correct += 1
    per_class = {
        lbl: ClassMetrics(lbl, support[lbl], predicted[lbl], correct[lbl]) for lbl in labels
    }
    n = len(golds)
    accuracy = n_correct / n if n else 0.0
    confusion_plain = {g: dict(d) for g, d in confusion.items()}
    return DimensionReport(name, n, accuracy, per_class, confusion_plain)


def score_classifier(labeled: list[LabeledContact], classify_fn: ClassifyFn) -> Scorecard:
    """Run ``classify_fn`` over the labeled set and compute the scorecard."""
    p_gold: list[str] = []
    p_pred: list[str] = []
    f_gold: list[str] = []
    f_pred: list[str] = []
    errors: list[tuple[LabeledContact, Persona, FocusArea]] = []
    for lc in labeled:
        pred_persona, pred_focus = classify_fn(lc)
        p_gold.append(lc.expected_persona.value)
        p_pred.append(pred_persona.value)
        f_gold.append(lc.expected_focus_area.value)
        f_pred.append(pred_focus.value)
        if pred_persona != lc.expected_persona or pred_focus != lc.expected_focus_area:
            errors.append((lc, pred_persona, pred_focus))
    persona = _score_dimension("persona", p_gold, p_pred, [e.value for e in Persona])
    focus = _score_dimension("focus_area", f_gold, f_pred, [e.value for e in FocusArea])
    return Scorecard(persona=persona, focus_area=focus, n=len(labeled), errors=errors)


def _format_dimension(d: DimensionReport) -> str:
    lines = [
        f"### {d.name} — accuracy {d.accuracy:.0%} (n={d.n}), macro-F1 {d.macro_f1:.2f}",
        "",
        "| class | support | precision | recall | F1 |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in d.per_class.values():
        if not (m.support or m.predicted):
            continue  # skip classes absent from both gold and predictions
        lines.append(
            f"| {m.label} | {m.support} | {m.precision:.0%} | {m.recall:.0%} | {m.f1:.2f} |"
        )
    return "\n".join(lines)


def format_scorecard(card: Scorecard) -> str:
    """Render a scorecard as markdown for the baseline doc."""
    parts = [
        f"## Classify accuracy scorecard (n={card.n})",
        "",
        f"- **Persona accuracy:** {card.persona.accuracy:.0%} "
        f"(macro-F1 {card.persona.macro_f1:.2f})",
        f"- **Focus-area accuracy:** {card.focus_area.accuracy:.0%} "
        f"(macro-F1 {card.focus_area.macro_f1:.2f})",
        "",
        _format_dimension(card.persona),
        "",
        _format_dimension(card.focus_area),
        "",
        f"### Mispredictions ({len(card.errors)})",
    ]
    if card.errors:
        parts.append("")
        parts.append("| contact | title | gold (persona/focus) | predicted |")
        parts.append("|---|---|---|---|")
        for lc, pp, pf in card.errors:
            parts.append(
                f"| {lc.full_name} | {lc.title} | "
                f"{lc.expected_persona.value}/{lc.expected_focus_area.value} | "
                f"{pp.value}/{pf.value} |"
            )
    else:
        parts.append("")
        parts.append("_None — every contact classified correctly._")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Live entrypoint — hits the network; excluded from coverage on purpose.
# --------------------------------------------------------------------------
def _build_live_classify_fn() -> ClassifyFn:  # pragma: no cover - live API
    from src.agents.finder import _classify_contact
    from src.core.config import get_anthropic_client, load_config
    from src.core.schemas import ContactCandidate

    cfg = load_config()
    client = get_anthropic_client(cfg.anthropic_api_key)

    def classify(lc: LabeledContact) -> tuple[Persona, FocusArea]:
        cand = ContactCandidate(
            full_name=lc.full_name,
            title=lc.title,
            company_slug=lc.company_slug,
            snippet=lc.snippet,
        )
        persona, focus, _ = _classify_contact(cand, lc.company_slug, client)
        return persona, focus

    return classify


def main() -> None:  # pragma: no cover - live entrypoint
    labeled = load_labeled_set()
    card = score_classifier(labeled, _build_live_classify_fn())
    print(format_scorecard(card))


if __name__ == "__main__":  # pragma: no cover
    main()
