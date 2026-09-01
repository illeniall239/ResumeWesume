/**
 * The Markdown subset the assistant writes.
 *
 * The security tests at the bottom are the ones that matter most. This text is
 * not just model output — the assistant quotes the user's resume, and that
 * resume came out of an uploaded PDF nobody vetted. Rendering it must never be
 * able to produce markup.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Markdown } from '@/chat/markdown';

function draw(text: string) {
  return render(<Markdown text={text} />).container;
}

describe('Markdown', () => {
  it('renders bold and italic', () => {
    const container = draw('That bullet is **vague** and a little *long*.');
    expect(container.querySelector('strong')?.textContent).toBe('vague');
    expect(container.querySelector('em')?.textContent).toBe('long');
    expect(container.textContent).toBe('That bullet is vague and a little long.');
  });

  it('renders inline code', () => {
    expect(draw('Call `add_bullet` next.').querySelector('code')?.textContent).toBe(
      'add_bullet'
    );
  });

  it('renders a heading without its hashes', () => {
    const container = draw('### Why?\nBecause it lacks a metric.');
    const heading = container.querySelector('.msg__heading');
    expect(heading?.textContent).toBe('Why?');
    expect(container.textContent).not.toContain('#');
  });

  it('renders a numbered list', () => {
    const container = draw(
      '1. **Vagueness**: too generic.\n2. **No metric**: nothing measured.'
    );
    const items = container.querySelectorAll('ol.msg__list li');
    expect(items).toHaveLength(2);
    expect(items[0].querySelector('strong')?.textContent).toBe('Vagueness');
    expect(container.textContent).not.toContain('1.');
  });

  it('renders a bulleted list', () => {
    const items = draw('- first\n- second\n- third').querySelectorAll('ul.msg__list li');
    expect(items).toHaveLength(3);
    expect(items[2].textContent).toBe('third');
  });

  it('keeps paragraphs apart', () => {
    const paragraphs = draw('First thought.\n\nSecond thought.').querySelectorAll('p');
    expect(paragraphs).toHaveLength(2);
  });

  it('keeps a single line break inside a paragraph', () => {
    // Models use a bare newline to mean a break, not a space.
    expect(draw('Line one\nLine two').querySelectorAll('br')).toHaveLength(1);
  });

  it('renders a fenced code block', () => {
    const pre = draw('Try:\n```\nmake api\nmake web\n```').querySelector('pre');
    expect(pre?.textContent).toBe('make api\nmake web');
  });

  it('leaves an unterminated marker as literal text while streaming', () => {
    // Deltas split anywhere, so half of a bold span is a normal intermediate
    // state and must not swallow the rest of the message.
    expect(draw('The weakest is **Why').textContent).toBe('The weakest is **Why');
  });

  it('does not italicise across snake_case identifiers', () => {
    // "build_script and my_project" is the reason underscore emphasis is not
    // supported at all: the pattern matches straight across the gap.
    const container = draw('The build_script and my_project were unchanged.');
    expect(container.querySelector('em')).toBeNull();
    expect(container.textContent).toBe(
      'The build_script and my_project were unchanged.'
    );
  });

  it('renders the real assistant reply legibly', () => {
    const real =
      'The weakest bullet is: *"Deployed a task-manager"*\n\n' +
      '**Why?**\n' +
      '1. **Vagueness**: the phrase is too generic.\n' +
      '2. **Lack of Metrics**: it does not quantify results.';
    const container = draw(real);
    expect(container.querySelectorAll('ol li')).toHaveLength(2);
    expect(container.textContent).not.toContain('**');
    expect(container.textContent).not.toContain('1.');
  });

  it('keeps a numbered list together across blank lines', () => {
    // Observed live: the model spaces its numbered points out, which produced
    // two separate lists and rendered both items as "1.".
    const container = draw(
      '1. **Vagueness**: too generic.\n\n2. **No metric**: nothing measured.'
    );
    expect(container.querySelectorAll('ol')).toHaveLength(1);
    expect(container.querySelectorAll('ol li')).toHaveLength(2);
  });

  it('resumes numbering where the model said it does', () => {
    const list = draw('3. third point\n4. fourth point').querySelector('ol');
    expect(list?.getAttribute('start')).toBe('3');
  });

  it('ends a list when real prose follows it', () => {
    const container = draw('1. first\n\nAnd now a closing thought.');
    expect(container.querySelectorAll('ol li')).toHaveLength(1);
    expect(container.textContent).toContain('And now a closing thought.');
  });

  it('is safe on empty input', () => {
    expect(draw('').textContent).toBe('');
  });
});

describe('Markdown safety', () => {
  it('renders HTML in the text as visible characters, not markup', () => {
    // The path that matters: a resume containing a script tag, quoted back by
    // the assistant. Building React elements makes this structural, not a
    // matter of escaping correctly.
    const container = draw('Your bullet says <script>alert(1)</script> oddly.');
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('<script>alert(1)</script>');
  });

  it('does not turn an image tag into an element', () => {
    const container = draw('It reads <img src=x onerror=alert(1)> here.');
    expect(container.querySelector('img')).toBeNull();
  });

  it('does not create a clickable link from a URL', () => {
    // A URL here can originate in an uploaded PDF; a live anchor is a phishing
    // surface bought for nothing.
    const container = draw('See [my site](https://evil.example.com) for more.');
    expect(container.querySelector('a')).toBeNull();
    render(<Markdown text="Visit https://evil.example.com now" />);
    expect(screen.queryByRole('link')).toBeNull();
  });
});
