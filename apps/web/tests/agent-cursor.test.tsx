/**
 * Where the pen is allowed to be.
 *
 * The pen is the one piece of chrome that moves, and it sits on top of the
 * résumé while the agent works. That buys it exactly one obligation: every
 * position it takes has to be one the server reported. A pointer that drifted
 * across a section nobody touched would be a fabricated claim about where the
 * work happened -- indistinguishable, to someone watching, from a real one.
 *
 * So these tests are mostly about restraint: the pen appears when the protocol
 * names a target, it distinguishes a read from a write using the tier the
 * server declared rather than a tool name copied into the client, and it
 * leaves the sheet on every path a turn can end by -- including the ones that
 * end badly.
 */

import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AgentCursor } from '@/canvas/agent-cursor';
import { applyEvent, type ChatMessage, targetOf } from '@/store/chat';
import { useStudio } from '@/store/studio';

/** Run one event through the reducer, the way the NDJSON reader does. */
function reduce(event: Record<string, unknown>): void {
  let message: ChatMessage = { id: 'a1', role: 'assistant', text: '', activity: [] };
  const patch = (change: (current: ChatMessage) => ChatMessage) => {
    message = change(message);
  };
  applyEvent(event as never, patch as never, (() => {}) as never, 'a1');
}

const attention = () => useStudio.getState().attention;

beforeEach(() => {
  useStudio.setState({ attention: null });
});

describe('what the arguments point at', () => {
  it('a node id is the target', () => {
    expect(targetOf({ nid: 'blt_9c21x', value: 'Shipped it' })).toBe('blt_9c21x');
  });

  it('a field edit resolves to the field, not to the whole entry', () => {
    // The same `nid.field` path `drafting` and `data-field` already speak, so
    // the pen lands on the one span rather than on the job that contains it.
    expect(targetOf({ nid: 'exp_004', field: 'title' })).toBe('exp_004.title');
  });

  it('a read names a section', () => {
    expect(targetOf({ section: 'experience', detail: 'outline' })).toBe('experience');
  });

  it('arguments that name no place point nowhere', () => {
    // `find_text` searches by words and its arguments say nothing about where
    // it looked. Guessing a location for it is exactly the invention this
    // whole layer exists to avoid.
    expect(targetOf({ query: 'the AWS bullet', limit: 5 })).toBeNull();
    expect(targetOf(undefined)).toBeNull();
  });
});

describe('the pen follows the protocol', () => {
  it('reaches the node before the edit lands', () => {
    // `tool_args` is emitted after validation and before the op is compiled.
    // That gap is the whole feature: the pen arrives first and the words
    // appear under it.
    reduce({ type: 'tool_start', call_id: 'c1', name: 'rewrite_text', tier: 'B' });
    reduce({ type: 'tool_args', call_id: 'c1', name: 'rewrite_text', args: { nid: 'blt_1' } });

    expect(attention()).toEqual({ target: 'blt_1', kind: 'writing' });
  });

  it('a read is marked as a read, from the tier the server declared', () => {
    // Not from the tool's name. A second copy of the tool table in the client
    // is a thing that goes stale the day a tool is added.
    reduce({ type: 'tool_start', call_id: 'c2', name: 'read_document', tier: 'R' });
    reduce({
      type: 'tool_args',
      call_id: 'c2',
      name: 'read_document',
      args: { section: 'skills' },
    });

    expect(attention()).toEqual({ target: 'skills', kind: 'reading' });
  });

  it('tracks the words as they stream in', () => {
    reduce({ type: 'drafting', call_id: 'c3', target: 'sum_00001', text: 'Engineer wi' });
    expect(attention()).toEqual({ target: 'sum_00001', kind: 'writing' });

    reduce({ type: 'drafting', call_id: 'c3', target: 'blt_7', text: 'Cut latency' });
    expect(attention()).toEqual({ target: 'blt_7', kind: 'writing' });
  });

  it('stays put when a call names no place', () => {
    reduce({ type: 'tool_start', call_id: 'c4', name: 'rewrite_text', tier: 'B' });
    reduce({ type: 'tool_args', call_id: 'c4', name: 'rewrite_text', args: { nid: 'blt_1' } });
    reduce({ type: 'tool_start', call_id: 'c5', name: 'find_text', tier: 'R' });
    reduce({ type: 'tool_args', call_id: 'c5', name: 'find_text', args: { query: 'AWS' } });

    // Not moved somewhere invented, and not blinked out either.
    expect(attention()).toEqual({ target: 'blt_1', kind: 'writing' });
  });

  it('is drawn as waiting, not as reading, before a target exists', () => {
    // The tempting version tours a few sections while the model thinks. It
    // looks busy and it is a lie: nothing has been read, and a viewer cannot
    // tell that pointer from one that means something.
    reduce({ type: 'assistant_delta', text: 'Let me take a look' });
    expect(attention()?.kind).not.toBe('reading');
  });

  it('follows a change whose tool named no node', () => {
    // `set_photo` takes an asset id and nothing else, so `tool_args` has no
    // place to point at and the pen used to stay where it was -- a picture
    // appeared on the sheet with no sign of where it came from.
    //
    // `touched` is derived from the ops rather than the arguments, so this
    // covers every tool including ones that do not exist yet.
    reduce({ type: 'tool_start', call_id: 'p1', name: 'set_photo', tier: 'C' });
    reduce({ type: 'tool_args', call_id: 'p1', name: 'set_photo', args: { asset: 'a'.repeat(64) } });

    // Nothing to point at yet: the arguments name no place, and inventing one
    // is exactly what this layer must not do.
    expect(attention()).toBeNull();

    reduce({
      type: 'patch_applied',
      call_id: 'p1',
      doc_version: 3,
      hash: 'h',
      touched: ['personal.photo'],
      ops: [],
    });

    expect(attention()).toEqual({ target: 'personal.photo', kind: 'writing' });
  });

  it('lands on whatever an ordinary edit touched', () => {
    reduce({
      type: 'patch_applied',
      call_id: 'p2',
      doc_version: 4,
      hash: 'h',
      touched: ['blt_9c21x'],
      ops: [],
    });

    expect(attention()).toEqual({ target: 'blt_9c21x', kind: 'writing' });
  });

  it('stays put when a patch touched nothing addressable', () => {
    reduce({ type: 'tool_start', call_id: 'p3', name: 'rewrite_text', tier: 'B' });
    reduce({ type: 'tool_args', call_id: 'p3', name: 'rewrite_text', args: { nid: 'blt_1' } });
    reduce({
      type: 'patch_applied',
      call_id: 'p3',
      doc_version: 5,
      hash: 'h',
      touched: [],
      ops: [],
    });

    expect(attention()).toEqual({ target: 'blt_1', kind: 'writing' });
  });

  it('leaves the sheet when the turn ends', () => {
    reduce({ type: 'tool_start', call_id: 'c6', name: 'rewrite_text', tier: 'B' });
    reduce({ type: 'tool_args', call_id: 'c6', name: 'rewrite_text', args: { nid: 'blt_1' } });
    reduce({ type: 'done', status: 'ok', doc_version: 4, hash: 'h' });

    expect(attention()).toBeNull();
  });

  it('forgets the tier of a call that has finished', () => {
    // Call ids are not reused, but a map that only ever grows is a leak in a
    // long session, and a stale tier would misread the next call's colour.
    reduce({ type: 'tool_start', call_id: 'c7', name: 'read_document', tier: 'R' });
    reduce({ type: 'done', status: 'ok', doc_version: 4, hash: 'h' });
    reduce({ type: 'tool_args', call_id: 'c7', name: 'read_document', args: { nid: 'blt_2' } });

    // No remembered tier means it is treated as a write, which is the safe
    // reading: it draws the pen down rather than claiming nothing will change.
    expect(attention()).toEqual({ target: 'blt_2', kind: 'writing' });
  });
});

describe('the pen on the page', () => {
  const host = (markup: string) => {
    const root = document.createElement('div');
    root.innerHTML = markup;
    document.body.appendChild(root);
    return { current: root } as React.RefObject<HTMLDivElement>;
  };

  /** Give an element a box, since jsdom measures everything as zero. */
  const measured = (el: Element, x: number, y: number, w: number, h: number) => {
    el.getBoundingClientRect = () => new DOMRect(x, y, w, h);
  };

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('draws nothing when the agent is not working', () => {
    useStudio.setState({ attention: null });
    const { container } = render(
      <AgentCursor host={host('<p data-nid="blt_1">Shipped it</p>')} zoom={1} />
    );

    expect(container.querySelector('.pen')).toBeNull();
  });

  it('never renders inside the document it is annotating', () => {
    // Both reasons are load-bearing and were paid for once already: every
    // candidate node is contentEditable and would commit anything inside it as
    // the user's own words, and this component is what Chromium prints.
    const root = host('<p data-nid="blt_1">Shipped it</p>');
    // jsdom does no layout, so the node is given one -- otherwise the pen
    // correctly declines to draw and the assertion below passes for the wrong
    // reason.
    measured(root.current!.querySelector('[data-nid="blt_1"]')!, 40, 20, 180, 18);
    measured(root.current!, 0, 0, 600, 800);

    useStudio.setState({ attention: { target: 'blt_1', kind: 'writing' } });
    const { container } = render(<AgentCursor host={root} zoom={1} />);

    // It drew -- so the absence below is a real placement, not an early return.
    expect(container.querySelector('.pen')).not.toBeNull();
    expect(root.current?.querySelector('.pen')).toBeNull();
    expect(root.current?.textContent).toBe('Shipped it');
  });

  it('sits past the end of the text, never over it', () => {
    // The résumé is the artifact of record. A piece of editing chrome does not
    // get to hide part of it in order to point at it.
    const root = host('<p data-nid="blt_1">Shipped it</p>');
    const node = root.current!.querySelector('[data-nid="blt_1"]')!;
    measured(node, 40, 20, 180, 18);
    measured(root.current!, 0, 0, 600, 800);

    useStudio.setState({ attention: { target: 'blt_1', kind: 'writing' } });
    const { container } = render(<AgentCursor host={root} zoom={1} />);

    // The two axes travel on separate elements so their different durations
    // bow the path; the position is the pair of them.
    const arm = container.querySelector('.pen-arm') as HTMLElement;
    const pen = container.querySelector('.pen') as HTMLElement;
    // Right edge of the node is 40 + 180 = 220, and the pen clears it.
    expect(arm.style.transform).toContain('translate3d(229px, 0, 0)');
    expect(pen.style.transform).toContain('translate3d(0, 20px, 0)');
  });

  it('comes down the moment the instruction is given, before any target', () => {
    // A local model can think for many seconds before it says anything. An
    // empty sheet through all of it reads as nothing having happened.
    const root = host('<p data-nid="blt_1">Shipped it</p>');
    measured(root.current!.querySelector('[data-nid="blt_1"]')!, 40, 20, 180, 18);
    measured(root.current!, 0, 0, 600, 800);

    useStudio.setState({ attention: { target: null, kind: 'waiting' } });
    const { container } = render(<AgentCursor host={root} zoom={1} />);

    expect(container.querySelector('.pen--waiting')).not.toBeNull();
  });

  it('waits in the margin, never on a word', () => {
    // Waiting is not pointing. Parking it on a word would claim that word is
    // about to change, which is precisely what has not been decided yet.
    const root = host('<p data-nid="blt_1">Shipped it</p>');
    measured(root.current!.querySelector('[data-nid="blt_1"]')!, 40, 20, 180, 18);
    measured(root.current!, 0, 0, 600, 800);

    useStudio.setState({ attention: { target: null, kind: 'waiting' } });
    const { container } = render(<AgentCursor host={root} zoom={1} />);

    const arm = container.querySelector('.pen-arm') as HTMLElement;
    // Left edge of the node is 40. Asserted as "outside it" rather than as an
    // exact offset: the gutter is a tuning value and has already moved once,
    // while being clear of the text is the actual requirement.
    const x = Number(/translate3d\((-?[\d.]+)px/.exec(arm.style.transform)![1]);
    expect(x).toBeLessThan(40);
  });

  it('holds its place rather than jumping when a target is not on screen', () => {
    // A node inside a collapsed frame, or one a coverage rule left unrendered.
    // The old failure here is a pointer parked on the sheet's top-left corner,
    // which reads as a bug rather than as a position.
    const root = host('<p data-nid="blt_1">Shipped it</p>');
    useStudio.setState({ attention: { target: 'ghost_9', kind: 'writing' } });
    const { container } = render(<AgentCursor host={root} zoom={1} />);

    expect(container.querySelector('.pen')).toBeNull();
  });
});
