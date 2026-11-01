"""Voice casting: two speakers of the same sex and language get different, deterministic voices."""
import itertools

import pytest

from products.animind_core import casting

SOCRATES = {"name": "Sócrates", "gender": "m", "born": -470, "persona": "filósofo da ironia, paciente"}
NIETZSCHE = {"name": "Friedrich Nietzsche", "gender": "m", "born": 1844, "persona": "filósofo provocativo, martelo"}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")


def test_two_men_in_pt_br_get_two_different_voices():
    a, b = casting.cast([SOCRATES, NIETZSCHE], "pt-BR")
    assert a["voice"] != b["voice"]
    assert {a["voice"], b["voice"]} <= set(casting.POOL["pt-BR"]["m"])
    assert a["label"] and b["label"] and a["label"] != b["label"]
    assert a["auto"] and b["auto"]


def test_two_women_in_pt_br_get_two_different_voices():
    a, b = casting.cast([{"name": "Hannah Arendt", "gender": "f", "born": 1906},
                         {"name": "Simone de Beauvoir", "gender": "f", "born": 1908}], "pt-BR")
    assert a["voice"] != b["voice"] and {a["voice"], b["voice"]} <= set(casting.POOL["pt-BR"]["f"])


def test_cast_is_deterministic_and_independent_of_speaker_order():
    first = casting.cast([SOCRATES, NIETZSCHE], "pt-BR")
    for _ in range(3):
        assert casting.cast([SOCRATES, NIETZSCHE], "pt-BR") == first
    swapped = casting.cast([NIETZSCHE, SOCRATES], "pt-BR")
    assert [swapped[1]["voice"], swapped[0]["voice"]] == [first[0]["voice"], first[1]["voice"]]


def test_same_traits_still_split_by_name_not_by_pitch():
    a, b = casting.cast([{"name": "Platão", "gender": "m", "born": -428}, {"name": "Aristóteles", "gender": "m", "born": -384}],
                        "pt-BR")
    assert a["voice"] != b["voice"] and a["pitchHz"] == b["pitchHz"]


def test_every_same_sex_pair_is_distinct_and_never_a_close_pair():
    names = ["Sócrates", "Platão", "Nietzsche", "Kant", "Machado de Assis", "Einstein", "Darwin", "Freud"]
    for x, y in itertools.combinations(names, 2):
        a, b = casting.cast([{"name": x, "gender": "m"}, {"name": y, "gender": "m"}], "pt-BR")
        assert a["voice"] != b["voice"], (x, y)
        assert frozenset((a["voice"], b["voice"])) not in casting.CLOSE, (x, y)


def test_override_wins_and_the_other_speaker_avoids_it():
    pin = casting.cast([NIETZSCHE], "pt-BR")[0]["voice"]
    a, b = casting.cast([SOCRATES, NIETZSCHE], "pt-BR", [None, {"voice": pin}])
    assert b["voice"] == pin and b["auto"] is False and a["voice"] != pin


def test_more_speakers_than_voices_falls_back_to_pitch_split():
    many = [{"name": f"Homem {i}", "gender": "m"} for i in range(len(casting.POOL["pt-BR"]["m"]) + 1)]
    picks = casting.cast(many, "pt-BR")
    pairs = {(p["voice"], p["pitchHz"]) for p in picks}
    assert len(pairs) == len(picks)
