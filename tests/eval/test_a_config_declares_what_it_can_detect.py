from __future__ import annotations

import textwrap
from pathlib import Path

from governed_bi.eval.provenance import PRIOR_DISCORDANT, PRIOR_OF, config_power_refusal
from governed_bi.registry.config_profiles import ConfigProfile, config_profile, load_config_profiles


def _profile(**over: object) -> ConfigProfile:
    base: dict[str, object] = dict(
        name="v6",
        description="a corpus release",
        under_test=frozenset({"corpus_release"}),
        corpus_content_hash="c" * 64,
        question_subset="1351:423a3f4b65fb",
    )
    base.update(over)
    return ConfigProfile(**base)  # type: ignore[arg-type]


def test_an_config_that_cannot_detect_its_hypothesis_is_refused() -> None:
    """1pp on 131 questions. The floor at the prior discordance is 9.6pp, so this config would spend
    its whole budget to report a null about its sample."""
    refusal = config_power_refusal(_profile(hypothesised_effect=0.01, readout="EX"), 131)
    assert refusal is not None
    assert "cannot detect its own hypothesis on EX" in refusal
    assert "0.0956" in refusal


def test_a_detectable_hypothesis_passes() -> None:
    assert config_power_refusal(_profile(hypothesised_effect=0.05, readout="EX"), 1351) is None


def test_an_config_that_declares_no_hypothesis_is_silent_rather_than_refused() -> None:
    """Every config on disk predates the field. Inventing an effect size so the check has something to
    check would put this module's number into a later quotation of the config's."""
    assert config_power_refusal(_profile(), 1351) is None


def test_an_effect_with_no_readout_is_refused() -> None:
    """MDE is in points of the whole population and two readouts' base rates differ by two orders
    of magnitude, so an effect size with no quantity attached cannot be compared to a floor. A
    draft of this design read a mechanism indicator's smaller MDE as the better instrument; it was
    a unit error, and naming the readout is what makes it visible."""
    refusal = config_power_refusal(_profile(hypothesised_effect=0.05), 1351)
    assert refusal is not None
    assert "no readout" in refusal
    assert "two orders of magnitude" in refusal


def test_the_refusal_says_the_discordance_is_an_estimate() -> None:
    """The number is carried from another repository's paired run. Presenting it as a measurement of
    *this* config is the mistake ``eval/power.py``'s docstring exists to head off, and the refusal is
    where a reader meets it."""
    refusal = config_power_refusal(_profile(hypothesised_effect=0.01, readout="EX"), 131)
    assert refusal is not None
    assert "ESTIMATE" in refusal
    assert "another repository" in refusal


def test_the_prior_is_the_one_pair_on_disk_and_not_a_round_number() -> None:
    """20 of 131, kept as two integers. A decimal `0.153` reads like a rate somebody measured on
    this tree; the fraction reads like what it is, which is the distinction `eval/power.py`'s
    docstring exists for. A decimal here would read like a measured rate."""
    assert (PRIOR_DISCORDANT, PRIOR_OF) == (20, 131)


def test_a_tiny_config_still_gets_at_least_one_discordant_pair() -> None:
    """``require_power`` refuses zero discordance as unmeasurable rather than as infinite
    precision, so a config small enough to round the estimate to zero must not read as a pass."""
    refusal = config_power_refusal(_profile(hypothesised_effect=0.5, readout="EX"), 3)
    assert refusal is not None, "n=3 cannot detect a 50pp effect and must not pass"


# Tests that build ConfigProfile skip the loader, which is how a dropped field stayed hidden.
# ``--config`` reaches the check only through the loader.


def _from_file(tmp_path: Path, name: str, body: str) -> ConfigProfile:
    """The config as ``--config NAME`` gets it: through ``config_profile``, which is the accessor
    ``tools/run_datalake_eval.py`` calls, off a TOML file the real loader parsed.

    Distinct filenames because ``load_config_profiles`` is ``lru_cache``d on its path.
    """
    path = tmp_path / f"{name}.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return config_profile(name, path=path)


def _declaring(tmp_path: Path, name: str, effect: float, readout: str) -> ConfigProfile:
    return _from_file(tmp_path, name, f"""
    [config.{name}]
    under_test = ["prompt_set"]
    hypothesised_effect = {effect}
    readout = "{readout}"
    corpus_content_hash = "86ed1dbf"
    question_subset = "1351:423a3f4b65fb"
    """)


def test_a_hypothesis_declared_in_configs_toml_reaches_the_check_and_refuses(tmp_path: Path) -> None:
    """The loader dropping the hypothesis made the check read ``None`` and abstain on every declared config."""
    refusal = config_power_refusal(_declaring(tmp_path, "tiny", 0.01, "EX"), 131)

    assert refusal is not None, (
        "configs.toml declared a 1pp hypothesis on 131 questions and the check did not see it"
    )
    assert "cannot detect its own hypothesis on EX" in refusal
    assert "0.0956" in refusal


def test_a_hypothesis_declared_in_configs_toml_can_also_pass(tmp_path: Path) -> None:
    """The other side of the same wire, so that the test above cannot be satisfied by a check that
    refuses everything. 15pp on 1 351 questions is above the floor at the same discordance
    rate."""
    assert config_power_refusal(_declaring(tmp_path, "big", 0.15, "EX"), 1351) is None


def test_the_readout_a_file_declares_is_the_one_in_the_refusal(tmp_path: Path) -> None:
    """MDE is denominated in points of the whole population, and a mechanism indicator with a
    2.15pp ceiling has 1.9 resolvable steps against EX's 28.5. The readout is not in the
    arithmetic, so if the loader drops it the numbers stay identical and only the sentence a human
    reads changes -- which is the failure mode that made this worth a test of its own."""
    refusal = config_power_refusal(
        _declaring(tmp_path, "mech", 0.01, "semantic_assurance"), 131
    )

    assert refusal is not None
    assert "on semantic_assurance" in refusal, (
        "the refusal named a different readout than the file declared"
    )


def test_the_shipped_configs_reach_the_check_and_it_abstains_on_all_of_them() -> None:
    """Silence on the shipped file is no hypothesis, which a dropped field would look like too."""
    for name, profile in load_config_profiles().items():
        assert profile.hypothesised_effect is None, (
            f"[config.{name}] now declares a hypothesis; this test's premise has changed"
        )
        assert config_power_refusal(profile, 1351) is None
