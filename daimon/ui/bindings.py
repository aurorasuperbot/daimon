"""BindingTable — declarative key → action mapping.

A simple dict wrapper with multi-key syntax (``"left,h"`` binds both keys
to the same action) and optional ``show`` metadata so we can render a
hotkey footer from the same source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Binding:
    keys: Tuple[str, ...]    # one or more key strings (e.g. ("left", "h"))
    action: str              # action name dispatched to Screen.on_action
    description: str = ""    # human-readable label for footer rendering
    show: bool = True        # hide hotkey footer chip when False


class BindingTable:
    """A set of Bindings with O(1) lookup by key.

    Construct from either a list of Binding objects or a dict literal:

        BindingTable({
            "p": "pull",
            "left,h": ("focus_prev", "Previous"),
            "q": ("quit", "Quit"),
        })

    Dict-form values can be either a bare action string or a tuple of
    (action, description). Multi-key entries split on comma.
    """

    def __init__(self,
                 bindings: "Iterable[Binding] | Dict[str, str | Tuple]",
                 ) -> None:
        self._bindings: List[Binding] = []
        self._by_key: Dict[str, Binding] = {}
        if isinstance(bindings, dict):
            for keys, value in bindings.items():
                show = True
                if isinstance(value, tuple):
                    if len(value) == 3:
                        action, desc, show = value
                    elif len(value) == 2:
                        action, desc = value
                    else:
                        raise ValueError(
                            f"BindingTable value tuple must be "
                            f"(action,) | (action, desc) | (action, desc, show); "
                            f"got {value!r}"
                        )
                else:
                    action, desc = value, ""
                key_tuple = tuple(k.strip() for k in keys.split(","))
                self._add(Binding(keys=key_tuple, action=action,
                                  description=desc, show=show))
        else:
            for b in bindings:
                self._add(b)

    def _add(self, binding: Binding) -> None:
        self._bindings.append(binding)
        for key in binding.keys:
            self._by_key[key] = binding

    def lookup(self, key: str) -> Optional[Binding]:
        return self._by_key.get(key)

    def visible(self) -> List[Binding]:
        """Bindings with ``show=True`` — for rendering the hotkey footer."""
        return [b for b in self._bindings if b.show]

    def all(self) -> List[Binding]:
        return list(self._bindings)
