"""Re-lay-out documents whose frames predate per-entry framing.

Run by hand:

    uv run python scripts/relayout.py            # report only
    uv run python scripts/relayout.py --apply    # write

Why this exists. The first version of ``autolayout`` created one frame per
*section*. A frame moves whole, so a section holding five jobs became a single
700pt box that could not share a page, and a two-page resume migrated to three.
Framing each entry separately restores the flowing renderer's own granularity,
where an entry was the unbreakable unit. Documents migrated before that change
kept the coarse layout, and nothing re-runs migration on an already-v2
document -- so they need one deliberate pass.

**It refuses to touch a document anybody has arranged.** Re-laying out means
discarding placement, so the script first checks whether every frame is still
where the automatic layout would have put it: full content width, at the left
margin, in document order. Anything else is somebody's work and is left alone
with a note, because a maintenance script silently flattening a design is a far
worse outcome than a resume that is one page longer than it needs to be.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio.config import settings  # noqa: E402
from studio.doc.autolayout import A4, layout  # noqa: E402
from studio.doc.schema import FrameElement, StudioDoc  # noqa: E402
from studio.persistence.repo import DocumentRepo  # noqa: E402

#: How far a frame may sit from the automatic position and still count as
#: untouched. Generous: the reflow pass writes measured heights, so vertical
#: positions legitimately differ from what autolayout guessed.
_TOLERANCE = 1.0


def entry_nids(doc: StudioDoc) -> set[str]:
    return {
        entry.nid
        for section in (doc.experience, doc.education, doc.projects)
        for entry in section
    }


def needs_relayout(doc: StudioDoc) -> bool:
    """True when some entry has no frame of its own."""
    if not doc.pages:
        return False
    bound = {
        element.ref
        for page in doc.pages
        for element in page.elements
        if isinstance(element, FrameElement)
    }
    return bool(entry_nids(doc) - bound)


def looks_arranged(doc: StudioDoc) -> str | None:
    """A reason this document looks hand-placed, or None.

    Only horizontal geometry is checked. Vertical positions are rewritten by
    the reflow pass on every open, so a differing ``y`` says nothing about
    whether a person moved anything; an ``x`` or a width that is not the
    automatic one does.
    """
    for page in doc.pages:
        for element in page.elements:
            if not isinstance(element, FrameElement):
                return f"page {page.nid} holds an image or shape"
            if abs(element.rect.x - A4.margin) > _TOLERANCE:
                return f"{element.nid} is not at the left margin"
            if abs(element.rect.w - A4.content_width) > _TOLERANCE:
                return f"{element.nid} is not full width"
            if element.rotation:
                return f"{element.nid} is rotated"
    return None


async def main(apply: bool) -> None:
    repo = DocumentRepo(settings.resolved_database_url())
    await repo.create_schema()
    try:
        states = await repo.list()
        print(f"{len(states)} document(s)\n")

        for state in states:
            doc = state.doc
            title = state.title[:28]

            if not needs_relayout(doc):
                print(f"  {title:30} up to date ({_frames(doc)} frames)")
                continue

            arranged = looks_arranged(doc)
            if arranged:
                print(f"  {title:30} SKIPPED -- arranged by hand: {arranged}")
                continue

            fresh = doc.model_copy(deep=True)
            fresh.pages = layout(fresh)
            print(
                f"  {title:30} {_frames(doc)} -> {_frames(fresh)} frames"
                + ("" if apply else "   (dry run)")
            )
            if apply:
                await repo.replace(state.id, fresh, reason="relayout")
    finally:
        await repo.dispose()

    if not apply:
        print("\nNothing written. Re-run with --apply.")


def _frames(doc: StudioDoc) -> int:
    return sum(len(page.elements) for page in doc.pages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    asyncio.run(main(parser.parse_args().apply))
