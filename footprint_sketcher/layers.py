"""Resolve supported KiCad wildcard layers for visibility and edit protection."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable


def expanded_layers(layers: Iterable[str]) -> frozenset[str]:
    result = set()
    for layer in layers:
        if layer in ("*.Cu", "*.Mask"):
            result.update(("F" + layer[1:], "B" + layer[1:]))
        else:
            result.add(layer)
    return frozenset(result)


def layers_locked(layers: Iterable[str], locked: Iterable[str]) -> bool:
    return bool(expanded_layers(layers) & expanded_layers(locked))


def visible_layers(layers: Iterable[str], hidden: Iterable[str]) -> frozenset[str]:
    return expanded_layers(layers) - expanded_layers(hidden)


def locked_content(pads, locked: Iterable[str]) -> Counter:
    """Keep multiplicity: removing one of two equal locked objects is an edit."""
    locked = expanded_layers(locked)
    return Counter(pad for pad in pads if expanded_layers(pad.layers) & locked)
