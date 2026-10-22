from pathlib import Path

import pytest

from apply_agent.domain.profile import CandidateProfile
from apply_agent.offers.profile import ProfileError, load_profile

EXAMPLE = Path(__file__).parents[2] / "profile.example.toml"


def test_no_file_means_an_empty_profile(tmp_path: Path) -> None:
    assert load_profile(tmp_path / "missing.toml") == CandidateProfile()


def test_the_example_profile_is_valid() -> None:
    profile = load_profile(EXAMPLE)
    assert profile.years_experience == 2
    assert profile.years_ceiling == 4
    assert "backend" in profile.target


@pytest.mark.parametrize(
    "content",
    ["years_experience = ", "years_experience = -1", 'unknown = "x"', "target = [1]"],
)
def test_a_broken_profile_fails_loudly(tmp_path: Path, content: str) -> None:
    path = tmp_path / "profile.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ProfileError, match=r"profile\.toml"):
        load_profile(path)
