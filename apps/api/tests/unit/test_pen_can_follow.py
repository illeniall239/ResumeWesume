"""Every tool names somewhere the pen can go.

The pen is the only thing on screen that says *where* the agent is working, and
it takes no position it was not given: `tool_args` names a validated target
before the edit is attempted, and `patch_applied` names what was touched after.

So a tool whose arguments name nothing addressable leaves the pen parked in the
margin until the change has already landed -- and one whose ops touch nothing
addressable leaves it parked for the whole turn. Four tools were in the first
group: `set_personal_info`, `add_bullet`, `reorder_bullets` and `set_section`.

This is the contract in the other direction: the client's `targetOf` reads a
fixed set of argument names, and this fails if a tool is added whose arguments
carry none of them. The mirror of this list lives in `web/src/store/chat.ts`.
"""

from __future__ import annotations

from studio.agent.tools import REGISTRY

#: Argument names the client can turn into a position on the sheet.
#:
#: Each resolves through `data-field`, `data-nid` or `data-section` in
#: `document-flow`. Kept in step with `targetOf`, which is what actually reads
#: them, and with `test_pen_follows_the_agent` on the web side.
ADDRESSABLE = frozenset(
    {"nid", "target", "section", "field", "parent", "key", "nids", "page"}
)

#: Tools whose arguments genuinely name nothing on the page.
#:
#: Not an exemption granted for convenience: each of these *creates* something,
#: so the thing the pen would point at does not exist when the arguments are
#: read. They are covered by `patch_applied.touched`, which is derived from the
#: ops themselves and so names the new node once there is one.
CREATES_ITS_OWN_TARGET = frozenset(
    {
        "add_experience",
        "add_education",
        "add_project",
        "add_skill",
        "add_skill_group",
        "add_section",
        "add_page",
        "fork_board",
        "switch_board",
        "rename_board",
        # Takes an asset id and nothing else. The photo holder it fills is
        # `personal.photo`, which the arguments never name -- this is the tool
        # `touched` was added for.
        "set_photo",
        # Takes the skill's text, which is not an id and matches no element.
        "remove_skill",
        # A search. It has no target until it has results, and being tier R it
        # changes nothing for the pen to point at.
        "find_text",
    }
)


def test_every_tool_gives_the_pen_something_to_aim_at() -> None:
    missing = []
    for name in sorted(REGISTRY.names):
        if name in CREATES_ITS_OWN_TARGET:
            continue
        spec = REGISTRY.get(name)
        fields = set(spec.Args.model_fields)
        if not (fields & ADDRESSABLE):
            missing.append(f"{name} takes {sorted(fields)}")

    assert not missing, (
        "these tools name nowhere the pen can go, so it stays in the margin "
        "while they work: " + "; ".join(missing)
    )


def test_the_creating_tools_are_all_real() -> None:
    # A name that has been removed or renamed would quietly exempt nothing and
    # hide the next tool that needs the exemption.
    assert CREATES_ITS_OWN_TARGET <= REGISTRY.names
