"""Configuration loading for the CHANGELOG preparation tool.

Parses ``changelog.yaml`` into typed dataclasses, applies defaults, validates
required fields, and merges CLI overrides. See specification section 4.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any, Dict, List, Mapping, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError(
        "PyYAML is required by changelog-tool. Install it via "
        "`pip install -r scripts/release-helper/requirements.txt`."
    ) from exc


# Defaults (specification section 4.1).
DEFAULT_LOCAL_PATH = "."
DEFAULT_TO_REF = "HEAD"
DEFAULT_SMALL_COMMIT = 50
DEFAULT_BUGFIX_SKIP = 200
DEFAULT_BATCH_SIZE = 20
DEFAULT_MAX_PROMPT_CHARS = 50000
DEFAULT_WORKDIR = ".changelog"

# Known top-level keys; unknown keys are warned about, not rejected, to keep
# the config forward-compatible.
_KNOWN_TOP_LEVEL_KEYS = frozenset(
    {"repo", "range", "thresholds", "llm", "core_team", "output"}
)


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or invalid."""


@dataclasses.dataclass
class RepoConfig:
    owner: str
    name: str
    github_url: str
    local_path: str = DEFAULT_LOCAL_PATH


@dataclasses.dataclass
class RangeConfig:
    from_ref: str
    to_ref: str = DEFAULT_TO_REF


@dataclasses.dataclass
class ThresholdsConfig:
    small_commit: int = DEFAULT_SMALL_COMMIT
    bugfix_skip: int = DEFAULT_BUGFIX_SKIP


@dataclasses.dataclass
class LlmConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS


@dataclasses.dataclass
class CoreTeamConfig:
    emails: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class OutputConfig:
    workdir: str = DEFAULT_WORKDIR


@dataclasses.dataclass
class Config:
    repo: RepoConfig
    range: RangeConfig
    thresholds: ThresholdsConfig
    llm: LlmConfig
    core_team: CoreTeamConfig
    output: OutputConfig
    source_path: Optional[str] = None
    warnings: List[str] = dataclasses.field(default_factory=list)

    def commit_url(self, sha: str) -> str:
        """Build a commit URL for the given SHA from ``repo.github_url``."""

        base = self.repo.github_url.rstrip("/")
        return f"{base}/commit/{sha}"


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"section '{name}' must be a mapping, got {type(value).__name__}")
    return value


def _require_str(section: Mapping[str, Any], key: str, section_name: str) -> str:
    value = section.get(key)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise ConfigError(f"missing required field '{section_name}.{key}'")
    if not isinstance(value, str):
        raise ConfigError(
            f"field '{section_name}.{key}' must be a string, got {type(value).__name__}"
        )
    return value


def _optional_str(
    section: Mapping[str, Any], key: str, section_name: str, default: str
) -> str:
    value = section.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(
            f"field '{section_name}.{key}' must be a string, got {type(value).__name__}"
        )
    return value


def _positive_int(
    section: Mapping[str, Any], key: str, section_name: str, default: int
) -> int:
    value = section.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"field '{section_name}.{key}' must be an integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ConfigError(f"field '{section_name}.{key}' must be a positive integer")
    return value


def _parse_repo(data: Mapping[str, Any]) -> RepoConfig:
    section = _require_mapping(data.get("repo"), "repo")
    return RepoConfig(
        owner=_require_str(section, "owner", "repo"),
        name=_require_str(section, "name", "repo"),
        github_url=_require_str(section, "github_url", "repo"),
        local_path=_optional_str(section, "local_path", "repo", DEFAULT_LOCAL_PATH),
    )


def _parse_range(data: Mapping[str, Any]) -> RangeConfig:
    section = _require_mapping(data.get("range"), "range")
    return RangeConfig(
        from_ref=_require_str(section, "from", "range"),
        to_ref=_optional_str(section, "to", "range", DEFAULT_TO_REF),
    )


def _parse_thresholds(data: Mapping[str, Any]) -> ThresholdsConfig:
    section = _require_mapping(data.get("thresholds"), "thresholds")
    return ThresholdsConfig(
        small_commit=_positive_int(section, "small_commit", "thresholds", DEFAULT_SMALL_COMMIT),
        bugfix_skip=_positive_int(section, "bugfix_skip", "thresholds", DEFAULT_BUGFIX_SKIP),
    )


def _parse_llm(data: Mapping[str, Any]) -> LlmConfig:
    section = _require_mapping(data.get("llm"), "llm")
    return LlmConfig(
        batch_size=_positive_int(section, "batch_size", "llm", DEFAULT_BATCH_SIZE),
        max_prompt_chars=_positive_int(
            section, "max_prompt_chars", "llm", DEFAULT_MAX_PROMPT_CHARS
        ),
    )


def _parse_core_team(data: Mapping[str, Any]) -> CoreTeamConfig:
    section = _require_mapping(data.get("core_team"), "core_team")
    raw = section.get("emails", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ConfigError("field 'core_team.emails' must be a list of strings")
    emails: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ConfigError(
                f"field 'core_team.emails' must contain only strings, got {type(item).__name__}"
            )
        emails.append(item)
    return CoreTeamConfig(emails=emails)


def _parse_output(data: Mapping[str, Any]) -> OutputConfig:
    section = _require_mapping(data.get("output"), "output")
    return OutputConfig(
        workdir=_optional_str(section, "workdir", "output", DEFAULT_WORKDIR),
    )


def load_config(path: str) -> Config:
    """Load and validate the configuration from ``path``.

    Raises :class:`ConfigError` on any problem (missing file, invalid YAML,
    missing required fields, wrong types).
    """

    if not os.path.exists(path):
        raise ConfigError(f"config file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc

    if raw is None:
        raise ConfigError(f"config file {path} is empty")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"config file {path} must contain a top-level mapping")

    data: Dict[str, Any] = dict(raw)

    warnings: List[str] = []
    for key in data:
        if key not in _KNOWN_TOP_LEVEL_KEYS:
            warnings.append(f"unknown top-level config key '{key}' (ignored)")

    config = Config(
        repo=_parse_repo(data),
        range=_parse_range(data),
        thresholds=_parse_thresholds(data),
        llm=_parse_llm(data),
        core_team=_parse_core_team(data),
        output=_parse_output(data),
        source_path=path,
        warnings=warnings,
    )
    return config


def apply_overrides(
    config: Config,
    *,
    from_ref: Optional[str] = None,
    to_ref: Optional[str] = None,
    workdir: Optional[str] = None,
) -> Config:
    """Apply non-None CLI overrides to a loaded config and return it."""

    if from_ref is not None:
        config.range.from_ref = from_ref
    if to_ref is not None:
        config.range.to_ref = to_ref
    if workdir is not None:
        config.output.workdir = workdir
    return config


__all__ = [
    "Config",
    "RepoConfig",
    "RangeConfig",
    "ThresholdsConfig",
    "LlmConfig",
    "CoreTeamConfig",
    "OutputConfig",
    "ConfigError",
    "load_config",
    "apply_overrides",
]
