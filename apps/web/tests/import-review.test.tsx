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

/** One frame on a page, with every field the renderer reads actually set. */
function frame(nid: string, ref: string, y: number, h: number) {
  return {
    nid,
    ref,
    rect: { x: 40, y, w: 515, h },
    rotation: 0,
    autogrow: 'height',
    visible: true,
    locked: false,
    pinned: false,
    style: {
      align: 'left',
      font_scale: 1,
      color: null,
      background: null,
      padding: 0,
      radius: 0,
      opacity: 1,
    },
  };
}

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
  // Every preview carries a page layout now -- the importer lays one out the
  // same way ``POST /documents`` does -- so the fixture does too.
  pages: [
    {
      nid: 'pag_aaaaa',
      size: 'A4',
      orientation: 'portrait',
      background: null,
      elements: [frame('frm_aaaaa', 'personal', 40, 60), frame('frm_bbbbb', 'summary', 110, 40)],
    },
  ],
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
    expect(screen.getByText('Nothing to check')).toBeInTheDocument();
  });

  it('will not let a parse be imported while it is still running', () => {
    // Confirming mid-parse would save a half-read resume.
    setState({ status: 'parsing', doc: null, resumeData: null });
    render(<ImportReview />);
    expect(screen.getByRole('button', { name: 'Import to editor' })).toBeDisabled();
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
    expect(screen.getByRole('button', { name: 'Import to editor' })).toBeEnabled();
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
    expect(screen.getByRole('button', { name: 'Import to editor' })).toBeEnabled();
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
    fireEvent.click(screen.getByRole('button', { name: 'Import to editor' }));
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith('/studio/doc_123'));
  });

  it('stays put and surfaces the reason when saving fails', async () => {
    setState({});
    useImport.setState({
      confirm: vi.fn().mockRejectedValue(new Error('500: the database is gone')),
    });

    render(<ImportReview />);
    fireEvent.click(screen.getByRole('button', { name: 'Import to editor' }));
    await vi.waitFor(() =>
      expect(screen.getByRole('button', { name: 'Import to editor' })).toBeEnabled()
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

describe('what the rail says came out', () => {
  it('names the first employer and the last, not only how many', () => {
    // "5 entries" is true of a read that found five jobs and of one that found
    // the same job five times. The span is the part that can be checked
    // against the original at a glance.
    setState({
      sections: [
        section({
          key: 'experience',
          status: 'parsed',
          data: {
            entries: [
              { company: 'Northwind Systems' },
              { company: 'Fablework' },
              { company: 'GEO TV Network' },
            ],
          },
        }),
      ],
    });
    render(<ImportReview />);
    expect(screen.getByText('Northwind Systems → GEO TV Network')).toBeInTheDocument();
    expect(screen.getByText('3 entries')).toBeInTheDocument();
  });

  it('names which contact details were found', () => {
    setState({
      sections: [
        section({
          key: 'contact',
          status: 'parsed',
          data: { email: 'alex@example.com', phone: '+1 512 555 0104', github: '' },
        }),
      ],
    });
    render(<ImportReview />);
    expect(screen.getByText('phone, email')).toBeInTheDocument();
  });

  it('keeps the count of what it did not list', () => {
    // A long certificate title must not eat the "+ N more" that says how much
    // is not on the line.
    setState({
      sections: [
        section({
          key: 'skills',
          status: 'parsed',
          data: {
            items: [
              'IBM Data Analysis with Python',
              'IBM - Python for Data Science',
              'Databases and SQL for Data Science',
              'Google Data Analytics',
            ],
          },
        }),
      ],
    });
    render(<ImportReview />);
    expect(screen.getByText(/\+ \d+ more$/)).toBeInTheDocument();
  });

  it('says nothing about a section it has not read yet', () => {
    setState({
      sections: [section({ key: 'experience', status: 'running', data: { entries: [] } })],
    });
    render(<ImportReview />);
    expect(screen.getByText('reading…')).toBeInTheDocument();
  });

  it('reports the read as one line, ending on what has not happened', () => {
    // The fact the screen exists to keep in front of somebody is that nothing
    // is saved. It is the last thing the ledger says.
    setState({
      filename: 'cv.pdf',
      pages: 3,
      sourceText: 'ALEX MORGAN\n\nEXPERIENCE\nNorthwind',
      sections: [section({ status: 'parsed' }), section({ order: 2, status: 'parsed' })],
      failed: 0,
    });
    render(<ImportReview />);
    expect(
      screen.getByText(
        'read cv.pdf — 3 pages, 2 sections, 3 lines, 0 unreadable — not saved'
      )
    ).toBeInTheDocument();
  });
});

describe('how the file was divided, against how it comes out', () => {
  it('says both, because they routinely differ', () => {
    // A résumé exported with a page break after the summary arrives as three
    // pages and reads as two once it is set in a template. Neither number is
    // wrong and neither is guessable from the other, so both are stated.
    setState({ pages: 3 });
    render(<ImportReview />);
    expect(screen.getByText(/read cv\.pdf — 3 pages/)).toBeInTheDocument();
    expect(screen.getByText('1 page once imported')).toBeInTheDocument();
  });
});

describe('looking at the source it was read from', () => {
  it('shows the print and nothing else to begin with', () => {
    setState({});
    render(<ImportReview />);
    expect(screen.getByText('Alex Morgan')).toBeInTheDocument();
    expect(screen.queryByText('ALEX MORGAN')).not.toBeInTheDocument();
  });

  it('swaps the print for the text it was read from', () => {
    setState({});
    render(<ImportReview />);
    fireEvent.click(screen.getByRole('button', { name: 'Source text' }));
    expect(screen.getByText('ALEX MORGAN')).toBeInTheDocument();
    expect(screen.queryByText('Alex Morgan')).not.toBeInTheDocument();
  });

  it('puts both on the board together', () => {
    setState({});
    render(<ImportReview />);
    fireEvent.click(screen.getByRole('button', { name: 'Side by side' }));
    expect(screen.getByText('ALEX MORGAN')).toBeInTheDocument();
    expect(screen.getByText('Alex Morgan')).toBeInTheDocument();
  });

  it('reports the magnification it is actually showing', () => {
    // Two documents do not fit in the room one was sized for, so the print
    // comes down -- and a readout still saying 100% would be a lie about the
    // thing the reader is being asked to check.
    setState({});
    render(<ImportReview />);
    expect(screen.getByText('100%')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Side by side' }));
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('offers neither view when there is no source text', () => {
    setState({ sourceText: '' });
    render(<ImportReview />);
    expect(screen.getByRole('button', { name: 'Source text' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Side by side' })).toBeDisabled();
  });
});

describe('the print as sheets', () => {
  it('draws pages, not one continuous column', () => {
    // The preview used to be the flowing renderer: a single A4-wide column of
    // everything, however long. The studio -- moments later, on the same
    // résumé -- drew sheets with page breaks, so the screen asking you to
    // approve a document was showing a different document from the one you got.
    setState({});
    const { container } = render(<ImportReview />);
    expect(container.querySelectorAll('.canvas-page')).toHaveLength(1);
    expect(container.querySelector('.spread__paper .page')).toBeNull();
  });

  it('falls back to flowing for a parse stashed before pages existed', () => {
    // sessionStorage outlives a deploy. A document with content and no sheets
    // to put it on still has to render -- the alternative is a blank board on
    // the one screen that exists to show you something.
    setState({ doc: { ...DOC, pages: [] } as never });
    const { container } = render(<ImportReview />);
    expect(container.querySelectorAll('.canvas-page')).toHaveLength(0);
    expect(screen.getByText('Alex Morgan')).toBeInTheDocument();
  });
});
