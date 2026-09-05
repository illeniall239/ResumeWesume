/**
 * What stays in the bubble once a turn is over.
 *
 * The sidebar holds the conversation; the revision block holds the document's
 * record -- same number as the mark on the sheet, with the outgoing and
 * incoming text in full. Listing every applied edit in both made the sidebar a
 * worse copy of a better record, and buried the rows that have no other home.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Revision } from '@/chat/chat-panel';
import type { ChatMessage } from '@/store/chat';

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'a1',
    role: 'assistant',
    text: 'Done.',
    activity: [
      {
        callId: 'c1',
        name: 'rewrite_text',
        tier: 'A',
        status: 'applied',
        label: 'rewrote a bullet',
      },
      {
        callId: 'c2',
        name: 'add_skill',
        tier: 'B',
        status: 'rejected',
        detail: 'Python is already in technical.',
      },
      {
        callId: 'c3',
        name: 'scale',
        tier: 'A',
        status: 'note',
        detail: 'This turn rewrote 150% of the resume.',
      },
    ],
    ...over,
  };
}

/** The rows outside the fold: the ones a reader is meant to act on. */
function alerts(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll('.revision__body > .items > .item')
  ) as HTMLElement[];
}

/** The rows inside the fold: the complete record of the turn. */
function log(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll('.fold--steps .item')) as HTMLElement[];
}

describe('the steps a finished turn keeps', () => {
  it('keeps applied edits out of the top level, where the revision block records them', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);

    expect(alerts(container).some((row) => /rewrote a bullet/.test(row.textContent ?? ''))).toBe(
      false
    );
  });

  it('keeps a refusal at the top level, where it has no other home', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);

    expect(
      alerts(container).some((row) => /already in technical/.test(row.textContent ?? ''))
    ).toBe(true);
  });

  it('keeps an advisory note at the top level', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);

    expect(alerts(container).some((row) => /rewrote 150%/.test(row.textContent ?? ''))).toBe(
      true
    );
  });

  it('shows everything while the turn is still running', () => {
    // A bubble that sat blank for thirty seconds would read as a hang.
    render(
      <Revision
        message={message({ status: 'streaming' })}
       
       
      />
    );

    expect(screen.getByText(/rewrote a bullet/)).toBeTruthy();
  });
});

describe('reasoning', () => {
  it('is shown without a setting, and starts closed', () => {
    // It used to need a header toggle: a setting to find, and to remember
    // having found. A closed disclosure says it is there and costs one line.
    const { container } = render(
      <Revision message={message({ status: 'ok', thinking: 'weighing it up' })} />
    );

    const details = container.querySelector('details.fold--reasoning');
    expect(details).toBeTruthy();
    expect((details as HTMLDetailsElement).open).toBe(false);
    expect(screen.getByText('Reasoning')).toBeTruthy();
  });

  it('is absent when the model returned none', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);
    expect(container.querySelector('details.fold--reasoning')).toBeNull();
  });
});

/**
 * The tool calls, folded.
 *
 * Reasoning and the calls that followed it answer the same kind of question --
 * how the answer was arrived at -- so they are shown the same way and in the
 * same place. What is deliberate here is the split: the fold is the complete
 * record, and the handful of rows left outside it are the ones that still need
 * an answer.
 */
describe('the tool-call fold', () => {
  it('holds every call the turn made, applied ones included', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);

    expect(log(container)).toHaveLength(3);
    expect(container.querySelector('.fold--steps')?.textContent).toMatch(/rewrote a bullet/);
  });

  it('starts closed, like the reasoning above it', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);

    const fold = container.querySelector('details.fold--steps') as HTMLDetailsElement;
    expect(fold.open).toBe(false);
    expect(screen.getByText('Tool calls')).toBeTruthy();
  });

  it('sits below the reasoning, not above it', () => {
    const { container } = render(
      <Revision message={message({ status: 'ok', thinking: 'weighing it up' })} />
    );

    const folds = Array.from(container.querySelectorAll('details.fold'));
    expect(folds.map((f) => f.className)).toEqual([
      'fold fold--reasoning',
      'fold fold--steps',
    ]);
  });

  it('is labelled the same whatever the turn did', () => {
    // A count in the label made the fold's own heading move between turns, and
    // the number it reported was of no use closed -- the rows that matter are
    // legible the moment it is open.
    const one = message({ status: 'ok' });
    const { container } = render(
      <Revision message={{ ...one, activity: one.activity.slice(0, 1) }} />
    );

    expect(container.querySelector('.fold--steps summary')?.textContent).toBe('Tool calls');
  });

  it('does not fold the work away while it is still happening', () => {
    // The live list is most of what makes an agent legible. Folding it as it
    // runs leaves a bubble that sits blank for thirty seconds.
    const { container } = render(<Revision message={message({ status: 'streaming' })} />);

    expect(container.querySelector('.fold--steps')).toBeNull();
    expect(alerts(container)).toHaveLength(3);
  });

  it('is absent on a turn that called nothing', () => {
    const { container } = render(
      <Revision message={message({ status: 'ok', activity: [] })} />
    );

    expect(container.querySelector('.fold--steps')).toBeNull();
  });
});
