/**
 * Editing the résumé by hand.
 *
 * The promise this holds to: whatever you bring, you can change a word, add a
 * line, take one away -- and everything you did not touch is exactly as it
 * was. Every case here is a way that failed.
 */

import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { StudioDoc } from '@/contracts/doc';
import DocumentFlow from '@/render/document-flow';
import { addLineAfter, removeLine } from '@/doc/lines';

const DOC = {
  schema_version: 2,
  template: 'plain',
  layout: 'stack',
  scaffold: false,
  unverified: [],
  personal: { name: 'Alex Morgan', title: 'Engineer', email: 'a@example.com' },
  summary: { nid: 'sum_00001', text: 'Backend engineer.', style: 'plain' },
  experience: [
    {
      nid: 'exp_11111',
      title: 'Senior Engineer',
      company: 'Northwind',
      location: '',
      years: '2021 - Present',
      bullets: [
        { nid: 'blt_aaaaa', text: 'Rebuilt the ledger.', style: 'bullet' },
        { nid: 'blt_bbbbb', text: 'Ran the migration.', style: 'bullet' },
      ],
    },
  ],
  education: [],
  projects: [],
  skills: [],
  custom: [],
  sections: [],
  blocks: [],
  pages: [],
} as unknown as StudioDoc;

function draw() {
  const onEditText = vi.fn();
  const onEditField = vi.fn();
  const onSplitLine = vi.fn();
  const onRemoveLine = vi.fn();
  const view = render(
    <DocumentFlow
      doc={DOC}
      editable
      onEditText={onEditText}
      onEditField={onEditField}
      onSplitLine={onSplitLine}
      onRemoveLine={onRemoveLine}
    />
  );
  return { view, onEditText, onEditField, onSplitLine, onRemoveLine };
}

/** The element for one node, since a bullet carries `data-nid`. */
const lineFor = (nid: string) => document.querySelector<HTMLElement>(`[data-nid="${nid}"]`)!;
const fieldFor = (target: string) =>
  document.querySelector<HTMLElement>(`[data-field="${target}"]`)!;

/** Put the caret at the end of an element, which is what `atEnd` reads. */
function caretToEnd(element: HTMLElement) {
  const range = document.createRange();
  range.selectNodeContents(element);
  range.collapse(false);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
}

describe('changing a word', () => {
  it('commits what was typed, once, on blur', () => {
    const { onEditText } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    line.textContent = 'Rebuilt the ledger, end to end.';
    fireEvent.blur(line);

    expect(onEditText).toHaveBeenCalledTimes(1);
    expect(onEditText).toHaveBeenCalledWith('blt_aaaaa', 'Rebuilt the ledger, end to end.');
  });

  it('says nothing when the words did not change', () => {
    // A click in and straight back out is not an edit, and committing one
    // would put a version in the undo stack for doing nothing.
    const { onEditText } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    fireEvent.blur(line);
    expect(onEditText).not.toHaveBeenCalled();
  });

  it('commits a field against its attribute, not its node', () => {
    const { onEditField } = draw();
    const title = fieldFor('exp_11111.title');
    fireEvent.focus(title);
    title.textContent = 'Staff Engineer';
    fireEvent.blur(title);
    expect(onEditField).toHaveBeenCalledWith('exp_11111.title', 'Staff Engineer');
  });
});

describe('Escape abandons the edit', () => {
  it('puts back what was there and commits nothing', () => {
    // Every dialog in this app takes Escape that way. The one place where the
    // stakes are a person's own words took it as "commit", so there was no way
    // out of a half-typed line except undo.
    const { onEditText } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    line.textContent = 'half a thought';
    fireEvent.keyDown(line, { key: 'Escape' });

    expect(line.textContent).toBe('Rebuilt the ledger.');
    fireEvent.blur(line);
    expect(onEditText).not.toHaveBeenCalled();
  });

  it('does not swallow the next real edit', () => {
    const { onEditText } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    fireEvent.keyDown(line, { key: 'Escape' });
    fireEvent.blur(line);

    fireEvent.focus(line);
    line.textContent = 'kept this time';
    fireEvent.blur(line);
    expect(onEditText).toHaveBeenCalledWith('blt_aaaaa', 'kept this time');
  });
});

describe('a change arriving from elsewhere while the caret is here', () => {
  it('is not committed back as if the user had typed it', () => {
    // The blur compared against the current prop, and a change landing from
    // elsewhere moves that -- so releasing the caret wrote the incoming text
    // straight back, and an edit rejected server-side reappeared on blur.
    const { onEditText, view } = draw();
    fireEvent.focus(lineFor('blt_aaaaa'));

    view.rerender(
      <DocumentFlow
        doc={
          {
            ...DOC,
            experience: [
              {
                ...DOC.experience[0],
                bullets: [
                  { nid: 'blt_aaaaa', text: 'CHANGED ELSEWHERE', style: 'bullet' },
                  DOC.experience[0].bullets[1],
                ],
              },
            ],
          } as unknown as StudioDoc
        }
        editable
        onEditText={onEditText}
      />
    );
    fireEvent.blur(lineFor('blt_aaaaa'));
    expect(onEditText).not.toHaveBeenCalled();
  });
});

describe('a one-line field never gains a line break', () => {
  it('refuses Enter', () => {
    // `plaintext-only` accepts a newline, and a résumé field is a data field:
    // one inside a job title reached the stored value and the PDF, where it
    // read as a rendering fault.
    const { onEditField } = draw();
    const title = fieldFor('exp_11111.title');
    fireEvent.focus(title);
    // `fireEvent` returns false when the handler called preventDefault.
    expect(fireEvent.keyDown(title, { key: 'Enter' })).toBe(false);
    fireEvent.blur(title);
    expect(onEditField).not.toHaveBeenCalled();
  });
});

describe('adding and removing a line', () => {
  it('Enter at the end of a bullet asks for the next one', () => {
    const { onSplitLine } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    caretToEnd(line);
    fireEvent.keyDown(line, { key: 'Enter' });
    expect(onSplitLine).toHaveBeenCalledWith('blt_aaaaa');
  });

  it('Enter in the middle of a sentence does nothing', () => {
    // It would have to split the words too, and a bullet cut in half by a
    // stray keypress is worse than a keypress that does nothing.
    const { onSplitLine } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    const range = document.createRange();
    range.setStart(line.firstChild!, 3);
    range.collapse(true);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);

    fireEvent.keyDown(line, { key: 'Enter' });
    expect(onSplitLine).not.toHaveBeenCalled();
  });

  it('Backspace in an empty bullet asks to close it', () => {
    const { onRemoveLine } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    line.textContent = '';
    fireEvent.keyDown(line, { key: 'Backspace' });
    expect(onRemoveLine).toHaveBeenCalledWith('blt_aaaaa');
  });

  it('Backspace in a bullet with words in it deletes a character', () => {
    const { onRemoveLine } = draw();
    const line = lineFor('blt_aaaaa');
    fireEvent.focus(line);
    fireEvent.keyDown(line, { key: 'Backspace' });
    expect(onRemoveLine).not.toHaveBeenCalled();
  });
});

describe('which ops those gestures mean', () => {
  it('a new line lands directly below, not at the end', () => {
    // Enter in the middle of a job's bullets means "another one here";
    // appending would move it past work being written between.
    const made = addLineAfter(DOC, 'blt_aaaaa')!;
    expect(made.ops).toHaveLength(1);
    expect(made.ops[0]).toMatchObject({
      op: 'insert_node',
      parent: 'exp_11111',
      index: 1,
    });
  });

  it('the new line is empty, so it shows its hint rather than borrowed words', () => {
    const made = addLineAfter(DOC, 'blt_aaaaa')!;
    expect((made.ops[0] as unknown as { node: { text: string } }).node.text).toBe('');
  });

  it('removing a line hands the caret to its neighbour', () => {
    const cut = removeLine(DOC, 'blt_bbbbb')!;
    expect(cut.ops).toEqual([{ op: 'remove_node', nid: 'blt_bbbbb' }]);
    expect(cut.focus).toBe('blt_aaaaa');
  });

  it('the last line of an entry is kept', () => {
    // Backspace is held down, and an entry emptying itself out from under the
    // cursor is not what anybody meant by it.
    const one = {
      ...DOC,
      experience: [{ ...DOC.experience[0], bullets: [DOC.experience[0].bullets[0]] }],
    } as unknown as StudioDoc;
    expect(removeLine(one, 'blt_aaaaa')).toBeNull();
  });

  it('a line nobody owns is left alone', () => {
    expect(addLineAfter(DOC, 'blt_nosuch')).toBeNull();
    expect(removeLine(DOC, 'blt_nosuch')).toBeNull();
  });
});

describe('an empty field is still somewhere to aim at', () => {
  it('carries the hint as an attribute, never as text', () => {
    // Drawn by CSS. A placeholder in the DOM is text, and the blur reads text,
    // so it would be committed as the value the moment somebody clicked in
    // and out again.
    draw();
    const location = fieldFor('exp_11111.location');
    expect(location.textContent).toBe('');
    expect(location.dataset.placeholder).toBe('Location');
  });
});

describe('the section headings', () => {
  it('are editable like everything else on the sheet', () => {
    // They were the one text on the page nobody could change: every op reaches
    // a nid, and a section is not a node -- it is an entry in `doc.sections`
    // saying what the résumé calls this part of itself.
    draw();
    for (const key of ['summary', 'experience']) {
      expect(fieldFor(`section.${key}`)).toBeTruthy();
    }
  });

  it('commit against the section key, not against a node', () => {
    const { onEditField } = draw();
    const heading = fieldFor('section.experience');
    fireEvent.focus(heading);
    heading.textContent = 'Selected Work';
    fireEvent.blur(heading);
    expect(onEditField).toHaveBeenCalledWith('section.experience', 'Selected Work');
  });

  it('take Escape the way every other field does', () => {
    const { onEditField } = draw();
    const heading = fieldFor('section.summary');
    fireEvent.focus(heading);
    heading.textContent = 'NOT THIS';
    fireEvent.keyDown(heading, { key: 'Escape' });
    fireEvent.blur(heading);
    expect(onEditField).not.toHaveBeenCalled();
  });

  it('stay a plain heading where nothing is editable', () => {
    // The print route and the flowing export render the same component with
    // `editable` unset, and a PDF must carry no editing affordance at all.
    const view = render(<DocumentFlow doc={DOC} />);
    expect(view.container.querySelector('[data-field="section.experience"]')).toBeNull();
    const headings = [...view.container.querySelectorAll('h2.section__title')].map(
      (node) => node.textContent
    );
    expect(headings).toContain('Experience');
  });
});
