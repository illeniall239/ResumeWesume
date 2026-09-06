/**
 * The line between pressing enter and the first word coming back.
 *
 * Nothing is on screen yet -- no prose, no tool call -- and a still line there
 * reads exactly like a hung one. The dots are the smallest thing that says the
 * wait is a wait, and they are the only animation in the sidebar, so what they
 * are allowed to do is worth pinning.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Revision } from '@/chat/chat-panel';
import type { ChatMessage } from '@/store/chat';

const css = readFileSync(resolve(process.cwd(), 'app/board.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  ''
);

/** Every `selector { ... }` in the sheet, in source order. Flat rules only. */
function rules(): { selector: string; body: string }[] {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
    selector: match[1].trim(),
    body: match[2],
  }));
}

const waiting: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  text: '',
  activity: [],
  status: 'streaming',
};

describe('the waiting line', () => {
  it('is drawn only while there is nothing else to show', () => {
    // The moment a word or a tool call arrives, that is the progress, and a
    // ticker beside it is a second thing claiming to be the news.
    const { container } = render(<Revision message={waiting} />);
    expect(container.querySelector('.ticker')).not.toBeNull();

    const { container: answering } = render(
      <Revision message={{ ...waiting, text: 'Tightening the second bullet' }} />
    );
    expect(answering.querySelector('.ticker')).toBeNull();
  });

  it('says "Working" once to a screen reader, not an animating ellipsis', () => {
    // `role="status"` announces the line when it appears. The dots are
    // decoration on that announcement, and read out they are three full stops.
    const { container } = render(<Revision message={waiting} />);
    const status = container.querySelector('[role="status"]')!;
    expect(status).not.toBeNull();
    expect(status.textContent).toContain('Working');
    expect(container.querySelector('.ticker')!.getAttribute('aria-hidden')).toBe('true');
  });

  it('is three dots that are always there', () => {
    // Not one dot that gains and loses siblings: a ticker that adds and
    // removes characters moves the text after it, and in a column of turns
    // that nudges the whole conversation on every beat.
    const { container } = render(<Revision message={waiting} />);
    const dots = container.querySelectorAll('.ticker span');
    expect(dots).toHaveLength(3);
    expect([...dots].map((dot) => dot.textContent).join('')).toBe('...');
  });
});

describe('the animation itself', () => {
  it('moves nothing but opacity', () => {
    // Everything else reflows. `content` changes the width of the run,
    // `transform` and `margin` move what is beside it, and this sits inline in
    // a paragraph rather than in a box of its own.
    const frames = /@keyframes ticker\s*\{([\s\S]*?)\n\}/.exec(css);
    expect(frames).not.toBeNull();
    const properties = [...frames![1].matchAll(/([a-z-]+)\s*:/g)].map((hit) => hit[1]);
    expect([...new Set(properties)]).toEqual(['opacity']);
  });

  it('never takes a dot all the way out', () => {
    // An ellipsis that vanishes a character at a time reads as text being
    // deleted rather than as something in progress.
    const frames = /@keyframes ticker\s*\{([\s\S]*?)\n\}/.exec(css);
    const stops = [...frames![1].matchAll(/opacity:\s*([\d.]+)/g)].map((hit) =>
      Number(hit[1])
    );
    expect(stops.length).toBeGreaterThan(1);
    expect(Math.min(...stops)).toBeGreaterThan(0);
  });

  it('stops for anyone who has asked not to be moved, and stays lit', () => {
    // Still legible, because someone who turned motion off still has to be
    // able to tell a wait from a hang.
    const reduced = css.slice(css.indexOf('prefers-reduced-motion'));
    const rule = /\.ticker span\s*\{([^}]*)\}/.exec(reduced);
    expect(rule).not.toBeNull();
    expect(rule![1]).toMatch(/animation:\s*none/);
    expect(rule![1]).toMatch(/opacity:\s*1/);
  });

  it('staggers the three so they read as one wave', () => {
    // Three dots pulsing together is a blink; offset, it is a direction.
    const delays = rules()
      .filter((rule) => /\.ticker span:nth-child/.test(rule.selector))
      .map((rule) => /animation-delay:\s*([\d.]+)s/.exec(rule.body)?.[1]);
    expect(delays).toHaveLength(2);
    expect(new Set(delays).size).toBe(2);
  });
});
