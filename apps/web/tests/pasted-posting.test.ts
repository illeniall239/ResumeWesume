/**
 * A job posting pasted into the chat.
 *
 * The way in used to be a button and a dialog: declare, before typing
 * anything, that the next thing you do is aiming rather than editing. What
 * people actually do is paste the advert and say "tailor this to it", so the
 * field is the only entry now and the paste has to be recognised for what it
 * is.
 *
 * Two things have to be true for that to be safe. It must not fire on an
 * instruction, however long -- and it must not fire on somebody pasting their
 * own résumé, which shares most of a job ad's vocabulary.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, setJobDescription: vi.fn() };
});

const started = vi.fn();
vi.mock('@/stream/ndjson', async () => {
  const actual = await vi.importActual<typeof import('@/stream/ndjson')>('@/stream/ndjson');
  return {
    ...actual,
    startTurn: (body: unknown) => {
      started(body);
      return { abort: new AbortController(), turnId: Promise.resolve('turn-1') };
    },
  };
});

import { setJobDescription } from '@/lib/api';
import { readsAsAPosting } from '@/chat/posting';
import { useChat } from '@/store/chat';
import { useStudio } from '@/store/studio';

const POSTING = `Senior Backend Engineer — Stripe

About the role
We are looking for an engineer to own payment infrastructure end to end. You
will work with a small team on systems that move money for millions of
businesses, and you will set the direction for how we run them.

Requirements
- Deep Kubernetes and Terraform experience
- Comfortable owning a service in production
- Strong written communication

Benefits include equity, health cover and a learning budget.`;

/** The same length and shape, written by the person rather than about them. */
const A_RESUME = `Alex Morgan
alex@example.com

Senior Engineer, Northwind, 2021 to present
- Rebuilt the ledger service end to end, cutting reconciliation time by half
- Ran the cluster migration across thirty services with no customer downtime
- Mentored four engineers through their first production on-call rotation

Engineer, Fablework, 2018 to 2021
- Built the billing pipeline that still carries the company's invoicing
- Wrote the internal deploy tool used by every team in the company

Skills: Python, Go, PostgreSQL, Kubernetes, Terraform, AWS`;

const stored = (text: string | null) =>
  ({
    id: 'doc-1',
    title: 'R',
    version: 4,
    hash: 'h',
    doc: { schema_version: 1, sections: [], personal: {}, pages: [] },
    job_description: text,
  }) as never;

beforeEach(() => {
  vi.clearAllMocks();
  useChat.setState({ messages: [], streaming: false, error: null, confirm: null });
  useStudio.setState({ documentId: 'doc-1', jobDescription: null, error: null });
});

describe('telling an advert from an instruction', () => {
  it('knows a job posting when it sees one', () => {
    expect(readsAsAPosting(POSTING)).toBe(true);
  });

  it('leaves an ordinary instruction alone', () => {
    expect(readsAsAPosting('tailor this to the posting')).toBe(false);
  });

  it('leaves a long instruction alone', () => {
    // Length on its own is not evidence. Somebody dictating a summary in one
    // breath writes prose, not a block of lines.
    const long = `Please rewrite my summary so it leads with the ledger work ${'and reads well '.repeat(40)}`;
    expect(long.length).toBeGreaterThan(400);
    expect(readsAsAPosting(long)).toBe(false);
  });

  it('does not mistake a résumé for the job it is aimed at', () => {
    // The one that would really hurt: aiming the sheet at itself. It is long,
    // it is a block of lines, and it shares the vocabulary -- which is why the
    // words looked for are the ones only an advert uses.
    expect(readsAsAPosting(A_RESUME)).toBe(false);
  });

  it('wants more than a single word in common', () => {
    const near = `Some notes about the work\n\n${'Requirements gathering took a while. '.repeat(20)}\nand that is all\nfor now`;
    expect(near.length).toBeGreaterThan(400);
    expect(readsAsAPosting(near)).toBe(false);
  });
});

describe('sending one', () => {
  it('aims the résumé at it, without a dialog in the way', async () => {
    vi.mocked(setJobDescription).mockResolvedValue(stored(POSTING));

    useChat.getState().send('doc-1', POSTING);

    expect(setJobDescription).toHaveBeenCalledWith('doc-1', POSTING);
  });

  it('carries it on the same turn, so the paste itself is not wasted', () => {
    // The store call is a round trip. Without this the turn the posting is
    // pasted in is the one turn that does not have it.
    vi.mocked(setJobDescription).mockResolvedValue(stored(POSTING));

    useChat.getState().send('doc-1', POSTING);

    expect(started.mock.calls[0][0].job_description).toBe(POSTING);
  });

  it('does not re-aim at a posting it is already aimed at', () => {
    useStudio.setState({ jobDescription: POSTING });

    useChat.getState().send('doc-1', POSTING);

    expect(setJobDescription).not.toHaveBeenCalled();
  });

  it('leaves an instruction as an instruction', () => {
    useChat.getState().send('doc-1', 'tighten my summary');

    expect(setJobDescription).not.toHaveBeenCalled();
  });

  it('clips what it carries forward as history', () => {
    // A posting is pasted into this box now, and six of those unabridged is
    // the whole context window gone -- on the one thing already sent in full
    // under <job_description>.
    useChat.setState({
      messages: [{ id: 'm1', role: 'user', text: `${POSTING}
${POSTING}`, activity: [] }],
    });

    useChat.getState().send('doc-1', 'now tailor it');

    const [carried] = started.mock.calls[0][0].history;
    expect(carried.content.length).toBeLessThanOrEqual(600);
    expect(carried.content.endsWith('…')).toBe(true);
  });
});
