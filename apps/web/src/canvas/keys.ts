/**
 * Which key presses belong to the sheet.
 *
 * The studio's shortcuts hang off the window rather than off a focused node,
 * because a selected box is not a focusable element and there is nothing else
 * to hang them on. That is right, and it means they answer keys pressed
 * anywhere on the page -- including in the conversation beside the sheet.
 *
 * Deferring to `contentEditable` and `textarea` covers the caret. It does not
 * cover reading: a click on the transcript moves no focus, so somebody who
 * clicked a reply and pressed Ctrl+A got every element on page one selected
 * and their text selection thrown away. Ctrl+C then copied nothing, and the
 * conversation looked like text that could not be copied at all.
 *
 * What the browser does record is where the last click landed, as the
 * selection's anchor -- and it records it for a click on plain text that
 * focused nothing. That is the signal for "the person is reading over there".
 */

/** The conversation column. Everything in it is prose to be read, not a sheet. */
const CONVERSATION = '.schedule';

/**
 * Whether a window-level shortcut should act on the document.
 *
 * `target` is the event's, `anchor` the current selection's anchor node --
 * `window.getSelection()?.anchorNode`, passed in rather than read here so this
 * stays a function of its inputs.
 */
export function meantForTheSheet(
  target: EventTarget | null,
  anchor: Node | null
): boolean {
  const node = target as HTMLElement | null;
  if (node?.isContentEditable) return false;
  if (
    typeof HTMLTextAreaElement !== 'undefined' &&
    (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement)
  ) {
    return false;
  }
  return !within(anchor, CONVERSATION);
}

/** Whether `node` sits inside something matching `selector`. */
function within(node: Node | null, selector: string): boolean {
  if (!node) return false;
  // A text node has no `closest`, and a selection anchor is usually one.
  const element =
    node.nodeType === Node.ELEMENT_NODE
      ? (node as Element)
      : node.parentElement;
  return element?.closest(selector) != null;
}

export default meantForTheSheet;
