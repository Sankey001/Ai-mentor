"""Lesson parts: each sentence of a lesson is labeled what/why/how/example/mistake/recap."""

import json

from backend.services import lesson_packs, lesson_parts, talk
from tests.test_lesson_packs import LOOPS, packs, prepare  # noqa: F401


def test_labels_are_read_and_never_spoken():
    text = (
        "[what] A loop repeats code. [WHY] It saves typing. [example] Think of a shopping "
        "list, one item at a time. [mistake] Don't forget to stop it. [recap] Loops repeat."
    )

    sentences, parts = lesson_parts.parse_labeled(text, talk.split_sentences)

    assert sentences == [
        "A loop repeats code.",
        "It saves typing.",
        "Think of a shopping list, one item at a time.",
        "Don't forget to stop it.",
        "Loops repeat.",
    ]
    assert parts == ["what", "why", "example", "mistake", "recap"]


def test_text_without_labels_has_no_parts_and_stray_labels_are_removed():
    sentences, parts = lesson_parts.parse_labeled(
        "A loop repeats code. [note] It saves typing.", talk.split_sentences
    )

    assert sentences == ["A loop repeats code.", "It saves typing."]
    assert parts is None


def test_parts_are_guessed_for_older_scripts():
    sentences = [
        "Today we learn about loops.",
        "They matter because they save typing.",
        "For example, imagine washing ten plates,",
        "one after another.",
        "You write the steps once and the loop repeats them.",
        "A common mistake is forgetting to stop the loop.",
        "Remember, a loop repeats work for you.",
    ]

    assert lesson_parts.guess_parts(sentences) == [
        "what", "why", "example", "example", "how", "mistake", "recap",
    ]


def test_a_new_script_saves_the_part_of_each_sentence(packs):
    packs["scripts"]["Loops"] = (
        "[what] A loop repeats code. [why] It saves typing. "
        "[example] Think of washing plates. [recap] Loops repeat."
    )
    prepare([LOOPS])

    pack = lesson_packs.Pack("asha", LOOPS)
    saved = json.loads(open(pack.script_path, encoding="utf-8").read())
    assert saved["sentences"][0] == "A loop repeats code."
    assert saved["parts"] == ["what", "why", "example", "recap"]
    assert [s["part"] for s in pack.playable()["sentences"]] == saved["parts"]
    assert all("[" not in s for s in saved["sentences"])


def test_the_prepared_lesson_prompt_asks_for_labels(monkeypatch):
    seen = []

    def stream(system_instruction, messages, **_kwargs):
        seen.append(system_instruction)
        return iter(["[what] Sure."])

    monkeypatch.setattr(talk, "stream_ollama_content", stream)
    list(talk.reply_pieces("ada", "", [], LOOPS, ("ollama", "gemma3"), prepared=True))
    list(talk.reply_pieces("ada", "", [], LOOPS, ("ollama", "gemma3")))

    assert "[example]" in seen[0] and "[example]" not in seen[1]


def test_a_sentence_only_a_little_too_long_is_not_cut():
    sentence = "Imagine a big company cafeteria where everyone can walk up and get whatever food " \
        "they need, without asking anyone on staff."

    assert talk.split_sentences(sentence) == [sentence]
