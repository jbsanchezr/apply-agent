"""Read the candidate's profile from a TOML file (D59).

The profile is personal, so it lives outside the repository, next to the
Gmail token. Without the file every offer is judged by its title alone; a
file that does not parse stops the app at startup rather than ranking with
half a profile. See ``profile.example.toml``.
"""

import tomllib
from pathlib import Path

from pydantic import ValidationError

from apply_agent.domain.profile import CandidateProfile


class ProfileError(ValueError):
    """The profile file exists but is not a valid profile."""


def load_profile(path: Path) -> CandidateProfile:
    if not path.is_file():
        return CandidateProfile()
    try:
        with path.open("rb") as file:
            return CandidateProfile.model_validate(tomllib.load(file))
    except (tomllib.TOMLDecodeError, ValidationError) as err:
        raise ProfileError(f"{path} is not a valid profile: {err}") from err
