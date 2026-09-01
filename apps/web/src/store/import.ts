/**
 * Import state.
 *
 * A third store beside `useStudio` and `useChat`, for the reason those two are
 * separate from each other: a section resolving every twenty seconds must not
 * re-render the document preview beside it.
 *
 * `applyImportEvent` is exported as a pure function, and that shapes the whole
 * module. There is no fetch mocking anywhere in this codebase and no MSW, so
 * the only way the wire protocol gets real test coverage is if applying an
 * event is separable from receiving one. `start` is then the only action that
 * touches the network, and everything interesting is a plain function from
 * (state, event) to a state patch.
 */

'use client';

import { create } from 'zustand';

import type { StudioDoc } from '@/contracts/doc';
import type { FailureCode, SectionStatus } from '@/ingest/events';
import { createDocument } from '@/lib/api';
import { cancelImport, startImport, type StreamEvent } from '@/stream/ndjson';

/** Survives a reload of the review page, which would otherwise discard a
 *  parse that took minutes of local model time to produce. */
const STASH_KEY = 'resumeresume.import';

export type ImportStatus =
  | 'idle'
  | 'uploading'
  | 'parsing'
  | 'review'
  | 'saving'
  | 'error';

export interface ImportSection {
  key: string;
  heading: string;
  order: number;
  status: SectionStatus;
  needsModel: boolean;
  chars: number;
  sourceText?: string;
  data?: Record<string, unknown>;
  code?: FailureCode;
  message?: string;
  ms?: number;
}

/**
 * The data half of the store, with no actions in it.
 *
 * Split out so `applyImportEvent` can be typed over exactly what it touches.
 * A reducer that demanded the actions too would make every test construct six
 * functions it never calls, or cast past the type -- and a cast is how a
 * reducer that quietly starts reading an action ends up shipping.
 */
export interface ImportData {
  status: ImportStatus;
  filename: string;
  error: string | null;
  warnings: string[];
  columns: number;
  pages: number;

  sections: ImportSection[];

  title: string;
  resumeData: Record<string, unknown> | null;
  doc: StudioDoc | null;
  sourceText: string;
  parsed: number;
  failed: number;
}

export interface ImportState extends ImportData {
  start: (file: File) => void;
  cancel: () => void;
  setTitle: (title: string) => void;
  confirm: () => Promise<string>;
  reset: () => void;
  hydrate: () => void;
}

const EMPTY: ImportData = {
  status: 'idle',
  filename: '',
  error: null,
  warnings: [],
  columns: 1,
  pages: 0,
  sections: [],
  title: '',
  resumeData: null,
  doc: null,
  sourceText: '',
  parsed: 0,
  failed: 0,
};

function text(event: StreamEvent, key: string): string {
  const value = event[key];
  return typeof value === 'string' ? value : '';
}

function num(event: StreamEvent, key: string): number {
  const value = event[key];
  return typeof value === 'number' ? value : 0;
}

/**
 * Find the section an update refers to.
 *
 * By key, except for the sections we could not classify: those all arrive as
 * `other` and are told apart only by the heading they were found under, so
 * without that a resume with two unknown sections would show one of them
 * twice.
 */
function locate(sections: ImportSection[], key: string, heading: string): number {
  if (key === 'other' && heading) {
    const exact = sections.findIndex(
      (section) => section.key === key && section.heading === heading
    );
    if (exact !== -1) return exact;
  }
  return sections.findIndex((section) => section.key === key);
}

function patchSection(
  sections: ImportSection[],
  index: number,
  patch: Partial<ImportSection>
): ImportSection[] {
  if (index === -1) return sections;
  const next = sections.slice();
  next[index] = { ...next[index], ...patch };
  return next;
}

/**
 * Apply one wire event to the store's state.
 *
 * Pure, total, and tolerant of an event for a section it has never heard of:
 * a reattached stream can legitimately begin part-way through, and dropping an
 * update is better than throwing inside a fetch loop that has no way to
 * report it.
 */
export function applyImportEvent(
  state: ImportData,
  event: StreamEvent
): Partial<ImportData> {
  switch (event.type) {
    case 'import_started':
      return {
        status: 'parsing',
        filename: text(event, 'filename'),
        pages: num(event, 'pages'),
        columns: num(event, 'columns'),
        warnings: Array.isArray(event.warnings) ? (event.warnings as string[]) : [],
      };

    case 'section_found': {
      const key = text(event, 'key');
      const heading = text(event, 'heading');
      if (locate(state.sections, key, heading) !== -1 && key !== 'other') {
        return {};
      }
      const section: ImportSection = {
        key,
        heading,
        order: num(event, 'order'),
        status: 'pending',
        needsModel: event.needs_model === true,
        chars: num(event, 'chars'),
      };
      return { sections: [...state.sections, section] };
    }

    case 'section_started':
      return {
        sections: patchSection(
          state.sections,
          locate(state.sections, text(event, 'key'), ''),
          { status: 'running' }
        ),
      };

    case 'section_parsed':
      return {
        sections: patchSection(
          state.sections,
          locate(state.sections, text(event, 'key'), ''),
          {
            status: 'parsed',
            data: (event.data ?? {}) as Record<string, unknown>,
            sourceText: text(event, 'source_text'),
            ms: num(event, 'ms'),
          }
        ),
      };

    case 'section_failed':
      return {
        sections: patchSection(
          state.sections,
          locate(state.sections, text(event, 'key'), ''),
          {
            status: 'failed',
            code: event.code as FailureCode,
            message: text(event, 'message'),
            sourceText: text(event, 'source_text'),
          }
        ),
      };

    case 'section_skipped':
      return {
        sections: patchSection(
          state.sections,
          locate(state.sections, text(event, 'key'), text(event, 'heading')),
          { status: 'skipped', sourceText: text(event, 'source_text') }
        ),
      };

    case 'import_ready':
      return {
        status: 'review',
        title: text(event, 'title'),
        resumeData: (event.resume_data ?? {}) as Record<string, unknown>,
        doc: (event.doc ?? null) as StudioDoc | null,
        sourceText: text(event, 'source_text'),
        parsed: num(event, 'parsed'),
        failed: num(event, 'failed'),
      };

    case 'error':
      return { status: 'error', error: text(event, 'message') || 'Import failed.' };

    default:
      return {};
  }
}

function stash(state: ImportData): void {
  try {
    sessionStorage.setItem(
      STASH_KEY,
      JSON.stringify({
        status: state.status,
        filename: state.filename,
        warnings: state.warnings,
        columns: state.columns,
        pages: state.pages,
        sections: state.sections,
        title: state.title,
        resumeData: state.resumeData,
        doc: state.doc,
        sourceText: state.sourceText,
        parsed: state.parsed,
        failed: state.failed,
      })
    );
  } catch {
    // Private mode, or a quota. Losing the stash costs a re-upload, which is
    // not worth failing the import over.
  }
}

function clearStash(): void {
  try {
    sessionStorage.removeItem(STASH_KEY);
  } catch {
    /* nothing to clear */
  }
}

let handle: { abort: () => void; turnId: Promise<string> } | null = null;

export const useImport = create<ImportState>((set, get) => ({
  ...EMPTY,

  start(file) {
    handle?.abort();
    clearStash();
    set({ ...EMPTY, status: 'uploading', filename: file.name });

    handle = startImport(file, {
      onEvent: (event) => {
        set((state) => applyImportEvent(state, event));
        if (event.type === 'import_ready') stash(get());
      },
      onError: (error) => set({ status: 'error', error: error.message }),
      onClose: () => {
        // A stream that ends without ever reaching review died somewhere we
        // did not get an error event for.
        const state = get();
        if (state.status === 'parsing' || state.status === 'uploading') {
          set({
            status: 'error',
            error: state.error ?? 'The import stopped before it finished.',
          });
        }
      },
    });
  },

  cancel() {
    // Tell the server to stop as well as dropping the connection: aborting the
    // fetch alone leaves the model still working through the sections.
    handle?.turnId
      .then((id) => {
        if (id) void cancelImport(id);
      })
      .catch(() => {});
    handle?.abort();
    handle = null;
  },

  setTitle(title) {
    set({ title });
    stash(get());
  },

  async confirm() {
    const { title, resumeData, sourceText } = get();
    if (!resumeData) throw new Error('There is nothing to import yet.');

    set({ status: 'saving', error: null });
    try {
      // Posts `resume_data`, never `doc`. The preview's node ids are throwaway;
      // real ones are minted server-side, in the one place that mints them.
      const created = await createDocument({
        title: title || 'Imported resume',
        resume_data: resumeData,
        source_markdown: sourceText,
      });
      clearStash();
      return created.id;
    } catch (error) {
      set({ status: 'review', error: (error as Error).message });
      throw error;
    }
  },

  reset() {
    handle?.abort();
    handle = null;
    clearStash();
    set({ ...EMPTY });
  },

  hydrate() {
    if (get().status !== 'idle') return;
    try {
      const raw = sessionStorage.getItem(STASH_KEY);
      if (!raw) return;
      set({ ...EMPTY, ...JSON.parse(raw) });
    } catch {
      clearStash();
    }
  },
}));
