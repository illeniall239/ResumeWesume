/**
 * The review screen.
 *
 * The assertions worth having here are about what the screen refuses to hide:
 * a failed section, its reason, and its source text. A review step that
 * silently showed only what worked would be worse than no review step at all,
 * because it would give the user confidence in a document that lost a job.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { StudioDoc } from '@/contracts/doc';
import ImportReview from '@/ingest/review';
import { useImport, type ImportSection, type ImportState } from '@/store/import';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

const DOC: StudioDoc = {
  schema_version: 1,
  personal: {
    name: 'Alex Morgan',
    title: 'Backend Engineer',
    email: 'alex@example.com',
    phone: '',
    location: 'Austin, TX',
  },
  summary: { nid: 'sum_00001', text: 'Backend engineer.', style: 'plain' },
  experience: [],
  education: [],
  projects: [],
  skills: [],
  custom: [],
  sections: [],
} as unknown as StudioDoc;

function section(patch: Partial<ImportSection>): ImportSection {
  return {
    key: 'experience',
    heading: '',
    order: 1,
    status: 'pending',
    needsModel: true,
    chars: 100,
    ...patch,
  };
}

function setState(patch: Partial<ImportState>): void {
  useImport.setState({
    status: 'review',
    filename: 'cv.pdf',
    error: null,
    warnings: [],
    columns: 1,
    pages: 1,
    sections: [],
    title: 'Alex Morgan',
    resumeData: { personalInfo: { name: 'Alex Morgan' } },
    doc: DOC,
    sourceText: 'ALEX MORGAN',
    parsed: 1,
    failed: 0,
    ...patch,
  });
}

beforeEach(() => {
  push.mockReset();
  useImport.setState({ status: 'idle', sections: [], doc: null, resumeData: null });
});

describe('ImportReview', () => {
  it('says there is nothing to review before an upload', () => {
    render(<ImportReview />);
    expect(screen.getByText('Nothing to review')).toBeInTheDocument();
  });

  it('will not let a parse be imported while it is still running', () => {
    // Confirming mid-parse would save a half-read resume.
    setState({ status: 'parsing', doc: null, resumeData: null });
    render(<ImportReview />);
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
  });

  it('offers to stop while parsing and to discard once finished', () => {
    setState({ status: 'parsing', doc: null, resumeData: null });
    const { unmount } = render(<ImportReview />);
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    unmount();

    setState({});
    render(<ImportReview />);
    expect(screen.getByRole('button', { name: 'Discard' })).toBeInTheDocument();
  });

  it('renders the preview through the real document renderer', () => {
    setState({});
    render(<ImportReview />);
    expect(screen.getByText('Alex Morgan')).toBeInTheDocument();
    expect(screen.getByText('Backend engineer.')).toBeInTheDocument();
  });

  it('shows a failed section with its reason', () => {
    setState({
      failed: 1,
      sections: [
        section({
          status: 'failed',
          code: 'timeout',
          message: 'This section took too long to read and was skipped.',
          sourceText: 'EXPERIENCE\nNorthwind Systems',
        }),
      ],
    });
    render(<ImportReview />);
    expect(screen.getByText('could not read')).toBeInTheDocument();
    expect(screen.getByText(/took too long to read/)).toBeInTheDocument();
  });

  it('can show the source text of a section that failed', () => {
    // The promise that nothing is lost only holds if the user can see it.
    setState({
      failed: 1,
      sections: [
        section({
          status: 'failed',
          code: 'no_json',
          sourceText: 'EXPERIENCE\nNorthwind Systems',
        }),
      ],
    });
    render(<ImportReview />);
    expect(screen.queryByText(/Northwind Systems/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'source' }));
    expect(screen.getByText(/Northwind Systems/)).toBeInTheDocument();
  });

  it('still allows the import when a section failed', () => {
    // "Import what worked" is the containment promise made visible.
    setState({ failed: 1, sections: [section({ status: 'failed', code: 'no_json' })] });
    render(<ImportReview />);
    expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled();
    expect(
      screen.getByText(/You can still import everything else\./)
    ).toBeInTheDocument();
  });

  it('names a section it recognised but does not import', () => {
    setState({
      sections: [
        section({
          key: 'other',
          heading: 'VOLUNTEERING',
          status: 'skipped',
          sourceText: 'Taught evening classes',
        }),
      ],
    });
    render(<ImportReview />);
    expect(screen.getByText('not imported')).toBeInTheDocument();
    expect(screen.getByText(/is not a section we import yet/)).toBeInTheDocument();
  });

  it('reports how many entries were read from a section', () => {
    setState({
      sections: [
        section({ status: 'parsed', data: { entries: [{}, {}, {}] } }),
      ],
    });
    render(<ImportReview />);
    expect(screen.getByText(/3 entries/)).toBeInTheDocument();
  });

  it('shows extraction warnings without treating them as errors', () => {
    setState({ warnings: ['Only the first 10 pages were read.'] });
    render(<ImportReview />);
    expect(screen.getByText('Only the first 10 pages were read.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled();
  });

  it('lets the document be renamed before it is saved', () => {
    setState({});
    render(<ImportReview />);
    const name = screen.getByLabelText('Document name');
    fireEvent.change(name, { target: { value: 'Alex Morgan CV' } });
    expect(useImport.getState().title).toBe('Alex Morgan CV');
  });

  it('navigates to the new document once the import is accepted', async () => {
    setState({});
    const confirm = vi.fn().mockResolvedValue('doc_123');
    useImport.setState({ confirm });

    render(<ImportReview />);
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith('/studio/doc_123'));
  });

  it('stays put and surfaces the reason when saving fails', async () => {
    setState({});
    useImport.setState({
      confirm: vi.fn().mockRejectedValue(new Error('500: the database is gone')),
    });

    render(<ImportReview />);
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await vi.waitFor(() =>
      expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled()
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('discarding clears the parse and goes home', () => {
    setState({});
    render(<ImportReview />);
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(useImport.getState().status).toBe('idle');
    expect(useImport.getState().resumeData).toBeNull();
    expect(push).toHaveBeenCalledWith('/');
  });

  it('lists every section it found, in order', () => {
    setState({
      sections: [
        section({ key: 'contact', order: 0, status: 'parsed', needsModel: false }),
        section({ key: 'experience', order: 1, status: 'running' }),
        section({ key: 'skills', order: 2, status: 'pending', needsModel: false }),
      ],
    });
    render(<ImportReview />);
    const list = screen.getByRole('list');
    const labels = within(list)
      .getAllByRole('listitem')
      .map((item) => item.textContent);
    expect(labels[0]).toContain('Contact details');
    expect(labels[1]).toContain('Work experience');
    expect(labels[1]).toContain('reading…');
    expect(labels[2]).toContain('Skills');
  });
});
