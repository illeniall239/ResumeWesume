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

describe('the control', () => {
  it('offers to add one when the sheet is aimed at nothing', () => {
    render(<TargetJob />);
    expect(screen.getByRole('button', { name: /add the job/i })).toBeInTheDocument();
  });

  it('names the posting once there is one', () => {
    useStudio.setState({ jobDescription: POSTING });
    render(<TargetJob />);
    expect(screen.getByText('Senior Backend Engineer — Stripe')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add the job/i })).not.toBeInTheDocument();
  });

  it('renders nothing before a document has loaded', () => {
    // The store's documentId is what every call here needs; without one the
    // control would offer an action that cannot go anywhere.
    useStudio.setState({ documentId: null });
    const { container } = render(<TargetJob />);
    expect(container).toBeEmptyDOMElement();
  });

  it('stores what was pasted', async () => {
    vi.mocked(setJobDescription).mockResolvedValue(stored(POSTING));
    render(<TargetJob />);
    fireEvent.click(screen.getByRole('button', { name: /add the job/i }));

    fireEvent.change(screen.getByPlaceholderText(/paste the job description/i), {
      target: { value: POSTING },
    });
    fireEvent.click(screen.getByRole('button', { name: /use this/i }));

    await waitFor(() => expect(setJobDescription).toHaveBeenCalledWith('doc-1', POSTING));
    await waitFor(() => expect(useStudio.getState().jobDescription).toBe(POSTING));
  });

  it('opens showing the posting it already has', () => {
    // So "read it again" and "replace it" are the same gesture, and neither
    // starts by wiping what is there.
    useStudio.setState({ jobDescription: POSTING });
    render(<TargetJob />);
    fireEvent.click(screen.getByRole('button', { name: /senior backend engineer/i }));
    expect(screen.getByPlaceholderText(/paste the job description/i)).toHaveValue(POSTING);
  });

  it('will not save what is already saved', () => {
    useStudio.setState({ jobDescription: POSTING });
    render(<TargetJob />);
    fireEvent.click(screen.getByRole('button', { name: /senior backend engineer/i }));
    expect(screen.getByRole('button', { name: /use this/i })).toBeDisabled();
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

  it('reads one out of a PDF', async () => {
    vi.mocked(setJobDescriptionFromPdf).mockResolvedValue(stored(POSTING));
    render(<TargetJob />);
    fireEvent.click(screen.getByRole('button', { name: /add the job/i }));

    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'posting.pdf', {
      type: 'application/pdf',
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(setJobDescriptionFromPdf).toHaveBeenCalledWith('doc-1', file));
    await waitFor(() => expect(useStudio.getState().jobDescription).toBe(POSTING));
  });

  it('reports a PDF it could not read, in the server’s own words', async () => {
    // A scan with no text layer is the common case, and "No text could be read
    // from that PDF" tells somebody what to do about it. A generic failure does
    // not.
    vi.mocked(setJobDescriptionFromPdf).mockRejectedValue(
      new Error('No text could be read from that PDF. It may be a scan.')
    );
    render(<TargetJob />);
    fireEvent.click(screen.getByRole('button', { name: /add the job/i }));

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'scan.pdf', { type: 'application/pdf' })] },
    });

    await waitFor(() => expect(useStudio.getState().error).toMatch(/may be a scan/));
    expect(useStudio.getState().jobDescription).toBeNull();
  });
});
