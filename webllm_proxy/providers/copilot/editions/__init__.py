"""Edition registry."""

from __future__ import annotations

from .base import Edition
from .consumer import ConsumerEdition
from .m365 import M365Edition

_REGISTRY: dict[str, type[Edition]] = {
    ConsumerEdition.name: ConsumerEdition,
    M365Edition.name: M365Edition,
}


def get_edition(name: str) -> Edition:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(f"unknown edition {name!r}; known: {sorted(_REGISTRY)}") from None


__all__ = ["ConsumerEdition", "Edition", "M365Edition", "get_edition"]
