"""Aiming an attack at a creature on the Scene — the half every seat shares.

A roll line that is an attack (``rules.targetable_attack``) rolls as it always did on
a left click. A **right click** offers the Scene's creatures; picking one rolls the
same attack *at* it. On a player's seat that also declares it to the GM
(:data:`KIND_ATTACK` on the action channel, the way an area attack is), and the GM's
app — which can see the target's Defense — judges the hit and lands the save once the
GM accepts (``ui/attack_gm.py``). The GM's own aimed attacks run straight through.

The target travels as the Scene entry's opaque ref, and the attack's spec carries the
declaration's ``action_id`` and the target's name in ``targets`` (all keys the server
already keeps, so no protocol change).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtWidgets import QMenu

from mm_companion.core.rules import RollSpec, targetable_attack

#: The action kind a player's aimed attack is declared to the GM under.
KIND_ATTACK = "attack"

#: The menu's heading over the targets, and what it says with nobody to aim at.
AIM_HEADING = "Attack a target:"
NO_TARGETS = "No one on the Scene to attack"
NO_SESSION = "Aiming at a target needs a session"


@dataclass(frozen=True)
class SceneTarget:
    """One creature on the Scene that an attack can be aimed at."""

    ref: str
    name: str
    #: The Scene entry's minion badge, when the GM lets the table see it.
    minion: bool = False


@dataclass(frozen=True)
class AttackDeclaration:
    """A player's aimed attack, as it travels to the GM."""

    action_id: str
    target_ref: str
    target_name: str
    spec: RollSpec
    source_name: str = ""

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "target_ref": self.target_ref,
            "target_name": self.target_name,
            "spec": self.spec.to_dict(),
            "source_name": self.source_name,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AttackDeclaration | None:
        if not isinstance(raw, dict):
            return None
        spec = RollSpec.from_dict(raw.get("spec"))
        action_id = raw.get("action_id")
        ref = raw.get("target_ref")
        if spec is None or not isinstance(action_id, str) or not action_id:
            return None
        if not isinstance(ref, str) or not ref:
            return None
        name = raw.get("target_name")
        source = raw.get("source_name")
        return cls(
            action_id=action_id,
            target_ref=ref,
            target_name=name if isinstance(name, str) else "",
            spec=spec,
            source_name=source if isinstance(source, str) else "",
        )


def aimed_spec(spec: RollSpec, action_id: str, target_name: str, source_name: str) -> RollSpec:
    """*spec* as the roll aimed at *target_name*: tied to its declaration by
    *action_id*, naming its target, and titled so the history says who at whom."""

    title = f"{source_name} → {target_name}" if source_name else f"→ {target_name}"
    return replace(
        spec, action_id=action_id, targets=(target_name,), title=title, actor=source_name
    )


def scene_targets(
    entries: list[dict], *, own_player_id: str = "", skip_ref: str = ""
) -> list[SceneTarget]:
    """The Scene's creatures an attack may be aimed at: everyone but the attacker."""

    found: list[SceneTarget] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("ref", ""))
        if not ref or ref == skip_ref:
            continue
        if own_player_id and entry.get("player_id") == own_player_id:
            continue
        found.append(
            SceneTarget(ref, str(entry.get("name", "")) or "?", entry.get("minion") is True)
        )
    return found


def add_target_actions(
    menu: QMenu,
    spec: RollSpec,
    targets: list[SceneTarget] | None,
    on_pick: Callable[[RollSpec, SceneTarget], None],
    *,
    icon_for: Callable[[str], object] | None = None,
) -> bool:
    """Put "Attack a target:" and one entry per creature on *menu* for an attack *spec*.

    *targets* ``None`` means there is no session to aim in. Returns whether anything
    was added (nothing is, for a line that is no attack).
    """

    if not targetable_attack(spec):
        return False
    heading = menu.addAction(AIM_HEADING)
    heading.setEnabled(False)
    if targets is None:
        menu.addAction(NO_SESSION).setEnabled(False)
        return True
    if not targets:
        menu.addAction(NO_TARGETS).setEnabled(False)
        return True
    for target in targets:
        text = f"{target.name} (minion)" if target.minion else target.name
        action = menu.addAction(text, lambda s=spec, t=target: on_pick(s, t))
        action.setToolTip(f"Roll this attack at {target.name}; the GM resolves it")
        if icon_for is not None:
            icon = icon_for(target.ref)
            if icon is not None:
                action.setIcon(icon)
    return True


def targets_for(widget) -> list[SceneTarget] | None:
    """Who an attack line on *widget* may be aimed at.

    A window that knows better says so through an ``aim_targets`` callable — the GM's
    NPC sheet, whose targets are the GM's own Scene (offline too) less the creature
    itself. Anywhere else it is the live session's Scene, or ``None`` without one.
    """

    current = widget
    while current is not None:
        provider = getattr(current, "aim_targets", None)
        if callable(provider):
            return provider()
        current = current.parentWidget()
    from mm_companion.ui.session_bridge import active_session

    return session_targets(active_session())


def session_targets(bridge, *, skip_ref: str = "") -> list[SceneTarget] | None:
    """The targets on *bridge*'s Scene, or ``None`` when there is no session."""

    if bridge is None or not getattr(bridge, "in_session", False):
        return None
    own = "" if getattr(bridge, "is_gm", False) else bridge.own_player_id()
    return scene_targets(bridge.scene(), own_player_id=own, skip_ref=skip_ref)
