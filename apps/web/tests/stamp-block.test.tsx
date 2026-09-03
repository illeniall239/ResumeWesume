/**
 * The consent gate must never show a data structure again.
 *
 * This is the moment the whole product rests on: the engine has refused to
 * apply a change until the person authorises it, and what they are shown is
 * how they decide. It used to render `JSON.stringify(args, null, 2)` into a
 * `<pre>`, which asked a job seeker under time pressure to audit a payload --
 * and the arguments do not even say what is being replaced, only what it would
 * become.
 *
 * These tests are the guard on that. They assert the rendered output, not the
 * source: what a person actually reads when the gate opens.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ChatPanel from '@/chat/chat-panel';
import { useChat } from '@/store/chat';
import { useStudio } from '@/store/studio';

const DOC = {
  schema_version: 2,
  personal: {
    name: 'Alex Morgan',
    title: 'Senior Backend Engineer',
    email: 'alex.morgan@example.com',
    phone: '+1-555-0142',
    location: 'Austin, TX',
    website: null,
    linkedin: null,
    github: null,
  },
  summary: { nid: 'sum_1', text: 'Backend engineer with eight years.' },
  experience: [],
  education: [],
  projects: [],
  skills: [],
  certifications: [],
  custom: [],
  pages: [],
} as never;

/** A Tier C proposal, exactly as `confirm_required` delivers one. */
function openTheGate(args: Record<string, unknown>) {
  useStudio.setState({ doc: DOC });
  useChat.setState({
    messages: [],
    streaming: false,
    error: null,
    confirm: {
      callId: 'call_1',
      tool: 'set_field',
      args,
      risk: 'This changes your contact details, which the employer will use to reach you.',
    },
  });
}

afterEach(() => {
  cleanup();
  useChat.setState({ confirm: null, messages: [] });
});

describe('the consent gate', () => {
  it('states the change in words rather than as its arguments', () => {
    openTheGate({ target: 'personal.location', value: 'Seattle, WA' });
    render(<ChatPanel documentId="doc_1" />);

    expect(screen.getByText(/changes your contact details/i)).toBeInTheDocument();
    // Both readings, so the decision is about a difference and not a value.
    expect(screen.getByText('Austin, TX')).toBeInTheDocument();
    expect(screen.getByText('Seattle, WA')).toBeInTheDocument();
  });

  it('never renders the raw tool arguments', () => {
    openTheGate({ target: 'personal.location', value: 'Seattle, WA' });
    const { container } = render(<ChatPanel documentId="doc_1" />);

    // The specific regression: a JSON dump of the payload. Braces, quoted keys
    // and the argument names themselves must all be absent from what is read.
    expect(container.querySelector('.stamp pre')).toBeNull();
    const text = container.textContent ?? '';
    expect(text).not.toContain('{');
    expect(text).not.toContain('"value"');
    expect(text).not.toContain('callId');
  });

  it('weights refusing the same as signing', () => {
    openTheGate({ target: 'personal.location', value: 'Seattle, WA' });
    render(<ChatPanel documentId="doc_1" />);

    // The safe choice must never be the harder one to find: both are real
    // buttons in the same block, not a button beside a link.
    const sign = screen.getByRole('button', { name: /sign and apply/i });
    const refuse = screen.getByRole('button', { name: /refuse/i });
    expect(sign).toBeInTheDocument();
    expect(refuse).toBeInTheDocument();
    expect(sign.tagName).toBe(refuse.tagName);
  });

  it('applies nothing until it is signed', () => {
    const approve = vi.fn();
    useChat.setState({ approveConfirm: approve });
    openTheGate({ target: 'personal.location', value: 'Seattle, WA' });
    render(<ChatPanel documentId="doc_1" />);

    expect(approve).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /sign and apply/i }));
    expect(approve).toHaveBeenCalledWith('doc_1');
  });

  it('reads a text node the same way it reads a field', () => {
    // `set_text` addresses a bare nid; `set_field` addresses `nid.attribute`.
    // Both have to produce a before, or half the gates show only an after.
    openTheGate({ nid: 'sum_1', value: 'Backend engineer with nine years.' });
    render(<ChatPanel documentId="doc_1" />);

    expect(screen.getByText('Backend engineer with eight years.')).toBeInTheDocument();
    expect(screen.getByText('Backend engineer with nine years.')).toBeInTheDocument();
  });
});
