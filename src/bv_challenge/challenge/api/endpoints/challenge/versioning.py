# -*- coding: utf-8 -*-

"""Release compatibility: current version plus at most one grace version.

DH decision: every component is versioned, the previous release stays
accepted for one release (the grace version), and anything else is rejected
instead of being read as some other version.

The page, task, evidence schema and attestation format are what this server
issued or produced, so it knows them. The collector and SDK versions are
claimed by the payload (``versions`` block). The scorer version comes back in
the signed scoring result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

COMPONENTS = (
    "page",
    "collector",
    "sdk",
    "scorer",
    "task",
    "evidence_schema",
    "attestation_format",
)

# Payloads from the collector released before the ``versions`` block existed
# carry only ``schemaVersion: "3"``. They are labelled explicitly as this
# version, and only accepted while it is the grace version.
UNVERSIONED_COLLECTOR = "3.0.0"


@dataclass(frozen=True)
class ComponentPolicy:
    current: str
    grace: str | None = None

    def accepts(self, version: Any) -> bool:
        return isinstance(version, str) and version in {self.current, self.grace} - {None}


@dataclass(frozen=True)
class VersionPolicy:
    components: Mapping[str, ComponentPolicy] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VersionPolicy":
        components: dict[str, ComponentPolicy] = {}
        for name in COMPONENTS:
            raw = data.get(name)
            if raw is None:
                raise ValueError(f"version policy is missing component {name!r}")
            if isinstance(raw, str):
                components[name] = ComponentPolicy(current=raw)
                continue
            accepted = list(raw)
            if not 1 <= len(accepted) <= 2:
                raise ValueError(f"{name}: one current version and at most one grace version")
            components[name] = ComponentPolicy(*accepted)
        return cls(components)

    def check(self, versions: Mapping[str, Any]) -> list[str]:
        """Names of the components whose version is missing or not accepted."""
        return [
            name
            for name in COMPONENTS
            if not self.components[name].accepts(versions.get(name))
        ]

    def current(self) -> dict[str, str]:
        return {name: policy.current for name, policy in self.components.items()}


# Shipped defaults; deployments override them through configuration.
CURRENT_VERSIONS = {
    "page": "2.0.0",
    "collector": "3.1.0",
    "sdk": "3.1.0",
    "scorer": "5.0.0",
    "task": "1",
    "evidence_schema": "3",
    "attestation_format": "bv-attest/1",
}
DEFAULT_POLICY = VersionPolicy.from_dict(
    {
        **CURRENT_VERSIONS,
        "collector": ["3.1.0", UNVERSIONED_COLLECTOR],
        "sdk": ["3.1.0", UNVERSIONED_COLLECTOR],
    }
)


def claimed_client_versions(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Collector and SDK versions as the payload states them.

    A payload without a ``versions`` block is the unversioned v3 collector;
    a ``versions`` block that is present but malformed claims nothing.
    """
    block = payload.get("versions")
    if block is None:
        return {"collector": UNVERSIONED_COLLECTOR, "sdk": UNVERSIONED_COLLECTOR}
    if not isinstance(block, Mapping):
        return {}
    return {"collector": block.get("collector"), "sdk": block.get("sdk")}


__all__ = [
    "COMPONENTS",
    "CURRENT_VERSIONS",
    "DEFAULT_POLICY",
    "UNVERSIONED_COLLECTOR",
    "ComponentPolicy",
    "VersionPolicy",
    "claimed_client_versions",
]
