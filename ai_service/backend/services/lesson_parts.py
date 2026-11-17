"""The parts of a lesson (what it is, why it matters, how it works, an example,
a common mistake, a recap), so the classroom can show a lesson as headed
paragraphs instead of one block of text.

A script written now names the part of each sentence with a label such as
"[example]" at its start; the labels are removed before anything is spoken.
Scripts written before that, and lessons taught live, get their parts guessed
from the words they use.
"""

from __future__ import annotations

import re

PARTS = ("what", "why", "how", "example", "mistake", "recap")
LABEL = re.compile(r"\[\s*(" + "|".join(PARTS) + r")\s*\]\s*", re.IGNORECASE)
ANY_LABEL = re.compile(r"\[\s*[a-z]{2,10}\s*\]\s*", re.IGNORECASE)
LABEL_INSTRUCTION = (
    " Start every sentence with the label of its part in square brackets, then the "
    "sentence: [what], [why], [how], [example], [mistake] or [recap]. The labels are "
    "not spoken."
)

_EXAMPLE = re.compile(
    r"\b(for example|for instance|imagine|picture|think of|think about|like a|like an|"
    r"just like|similar to|say you|let's say|suppose)\b",
    re.IGNORECASE,
)
_MISTAKE = re.compile(
    r"\b(mistake|be warned|careful|avoid|pitfall|common error|trap|don't|do not|"
    r"never|beginners|tempted|bad habit|the wrong|go wrong)\b",
    re.IGNORECASE,
)
_RECAP = re.compile(
    r"^(so,|so |remember|in short|to sum|to recap|overall|in summary|that's|"
    r"that is|finally|always remember)",
    re.IGNORECASE,
)
_GOES_ON = re.compile(r"^(it|its|it's|this|that|they|these|and|so|then|in the same way)\b",
                      re.IGNORECASE)
_WHY = re.compile(
    r"\b(why|matters?|important|powerful|useful|without|helps?|lets you|allows you|"
    r"saves?|benefit)\b",
    re.IGNORECASE,
)


def parse_labeled(text: str, split) -> tuple[list[str], list[str] | None]:
    """Split a labeled script into spoken sentences and the part of each.

    ``split`` cuts text into spoken sentences (``talk.split_sentences``). The
    parts are None when the text has (almost) no labels; any stray label is
    still removed, so it is never spoken.
    """
    pieces = LABEL.split(" ".join(text.split()))
    # pieces: [text before the first label, label, text, label, text, ...]
    labeled = [(pieces[i].lower(), pieces[i + 1]) for i in range(1, len(pieces) - 1, 2)]
    sentences, parts = [], []
    for sentence in split(ANY_LABEL.sub("", pieces[0])):
        sentences.append(sentence)
        parts.append(None)
    for part, chunk in labeled:
        for sentence in split(ANY_LABEL.sub("", chunk)):
            sentences.append(sentence)
            parts.append(part)
    if not sentences or sum(part is not None for part in parts) < len(sentences) / 2:
        return sentences, None
    # A sentence before the first label belongs with the one after it.
    for index, part in enumerate(parts):
        if part is None:
            parts[index] = next((p for p in parts[index:] if p), "what")
    return sentences, parts


def whole_sentences(pieces: list[str]) -> list[list[int]]:
    """Group spoken pieces into written sentences: a long sentence is spoken in
    pieces cut at a comma, and only its last piece ends with a full stop."""
    groups: list[list[int]] = []
    for index, piece in enumerate(pieces):
        if groups and not pieces[groups[-1][-1]].rstrip().endswith((".", "!", "?")):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def guess_parts(pieces: list[str]) -> list[str]:
    """Guess each spoken piece's part from its words, for scripts without labels.
    All pieces of one written sentence get the same part."""
    groups = whole_sentences(pieces)
    sentences = [" ".join(pieces[i] for i in group) for group in groups]
    parts: list[str] = []
    count = len(sentences)
    for index, sentence in enumerate(sentences):
        last = index == count - 1
        if last and count >= 4 and (_RECAP.match(sentence) or index > 0):
            part = "recap"
        elif index > 0 and index >= count - 2 and _RECAP.match(sentence):
            part = "recap"
        elif index > 0 and _MISTAKE.search(sentence):
            part = "mistake"
        elif index > 0 and _EXAMPLE.search(sentence):
            part = "example"
        elif index == 0:
            part = "what"
        elif _RECAP.match(sentence) and index >= count - 2:
            part = "recap"
        elif parts[-1] in ("what", "why") and _WHY.search(sentence):
            part = "why"
        elif parts[-1] == "mistake" or (parts[-1] == "example" and _GOES_ON.match(sentence)):
            part = parts[-1]  # the example or warning goes on
        else:
            part = "how"
        parts.append(part)
    return [parts[number] for number, group in enumerate(groups) for _ in group]
