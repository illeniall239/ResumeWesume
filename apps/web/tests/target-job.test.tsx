/**
 * The job a résumé is aimed at.
 *
 * A standing fact about the document, not a field on a message: tailoring is a
 * conversation — you ask, you read it back, you ask again — and a posting
 * carried on the turn survived exactly one exchange, after which every
 * follow-up worked with no idea what the sheet was being aimed at.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    setJobDescription: vi.fn(),
    setJobDescriptionFromPdf: vi.fn(),
  };
});

import { setJobDescription, setJobDescriptionFromPdf } from '@/lib/api';
import ChatPanel from '@/chat/chat-panel';
import { TargetJob, headline } from '@/chat/target-job';
import { useStudio } from '@/store/studio';

const POSTING = `Senior Backend Engineer — Stripe

We are looking for deep Kubernetes and Terraform experience.`;

/** What the server sends back after storing one. */
const stored = (text: string | null) =>
  ({
    id: 'doc-1',
    title: 'R',
    version: 4,
    hash: 'h',
    doc: { schema_version: 1, sections: [], personal: {}, pages: [] },
    updated_at: '',
    job_description: text,
  }) as never;

beforeEach(() => {
  vi.clearAllMocks();
  useStudio.setState({ documentId: 'doc-1', jobDescription: null, error: null });
});

describe('naming the posting', () => {
  it('uses the first line with real words in it', () => {
    // Job boards put the title there essentially always, and when they do not
    // this still shows the first thing a person would read.
    expect(headline(POSTING)).toBe('Senior Backend Engineer — Stripe');
  });

  it('skips leading blank lines and stray punctuation', () => {
    expect(headline('\n\n  -  \nStaff Engineer, Payments\n')).toBe('Staff Engineer, Payments');
  });

  it('shortens a first line somebody pasted a whole paragraph into', () => {
    const name = headline('x'.repeat(400));
    expect(name.length).toBeLessThanOrEqual(60);
    expect(name.endsWith('…')).toBe(true);
  });

  it('has nothing to say about nothing', () => {
    expect(headline(null)).toBe('');
    expect(headline('   ')).toBe('');
  });
});

describe('the line above the field', () => {
  it('says nothing at all until there is a posting', () => {
    // There was a button here offering to add one. It asked somebody to
    // declare, before typing anything, that the next thing they did was
    // aiming rather than editing -- a step in front of the thing they were
    // going to do anyway, which is to paste the advert into the field.
    const { container } = render(<TargetJob />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the posting once there is one', () => {
    useStudio.setState({ jobDescription: POSTING });
    render(<TargetJob />);
    expect(screen.getByText('Senior Backend Engineer — Stripe')).toBeInTheDocument();
  });

  it('renders nothing before a document has loaded', () => {
    useStudio.setState({ documentId: null, jobDescription: POSTING });
    const { container } = render(<TargetJob />);
    expect(container).toBeEmptyDOMElement();
  });

  it('is a name, not a way back into a dialog', () => {
    // The posting is in the transcript, where it was pasted. The only thing
    // to do here is stop aiming at it.
    useStudio.setState({ jobDescription: POSTING });
    render(<TargetJob />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName(/stop aiming/i);
  });

  it('stops aiming at a job with one press', async () => {
    // Empty text is how the server is told; there is no separate remove call,
    // because "aimed at nothing" is not a different kind of state.
    vi.mocked(setJobDescription).mockResolvedValue(stored(null));
    useStudio.setState({ jobDescription: POSTING });
    render(<TargetJob />);

    fireEvent.click(screen.getByRole('button', { name: /stop aiming/i }));

    await waitFor(() => expect(setJobDescription).toHaveBeenCalledWith('doc-1', ''));
    await waitFor(() => expect(useStudio.getState().jobDescription).toBeNull());
  });
});

describe('a posting read out of a PDF', () => {
  it('is stored the same way a pasted one is', async () => {
    // The drop target is the composer; this is the store action behind it.
    vi.mocked(setJobDescriptionFromPdf).mockResolvedValue(stored(POSTING));
    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'posting.pdf', {
      type: 'application/pdf',
    });

    await useStudio.getState().aimAtPdf(file);

    expect(setJobDescriptionFromPdf).toHaveBeenCalledWith('doc-1', file);
    expect(useStudio.getState().jobDescription).toBe(POSTING);
  });

  it('reports one it could not read, in the server’s own words', async () => {
    // A scan with no text layer is the common case, and "No text could be read
    // from that PDF" tells somebody what to do about it. A generic failure does
    // not.
    vi.mocked(setJobDescriptionFromPdf).mockRejectedValue(
      new Error('No text could be read from that PDF. It may be a scan.')
    );

    await useStudio.getState().aimAtPdf(new File(['x'], 'scan.pdf', { type: 'application/pdf' }));

    expect(useStudio.getState().error).toMatch(/may be a scan/);
    expect(useStudio.getState().jobDescription).toBeNull();
  });
});

describe('dropping a PDF on the composer', () => {
  it('aims the résumé at the posting inside it', async () => {
    // The only file this pane takes, so the whole box is the target and there
    // is no button in front of it. The dialog that used to hold this is gone.
    vi.mocked(setJobDescriptionFromPdf).mockResolvedValue(stored(POSTING));
    const { container } = render(<ChatPanel documentId="doc-1" />);
    const composer = container.querySelector('.composer') as HTMLElement;

    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'posting.pdf', {
      type: 'application/pdf',
    });
    fireEvent.drop(composer, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(setJobDescriptionFromPdf).toHaveBeenCalledWith('doc-1', file));
  });

  it('ignores anything that is not a PDF', () => {
    // A dragged image is for the résumé, not for the posting, and guessing
    // wrong here would silently re-aim the sheet at a photograph.
    const { container } = render(<ChatPanel documentId="doc-1" />);
    const composer = container.querySelector('.composer') as HTMLElement;

    fireEvent.drop(composer, {
      dataTransfer: { files: [new File(['x'], 'headshot.png', { type: 'image/png' })] },
    });

    expect(setJobDescriptionFromPdf).not.toHaveBeenCalled();
  });
});
