/**
 * What the client sends when undo is pressed, and what it sends that undo will
 * later have to walk past.
 *
 * The bugs here were not in the chooser. They were versions arriving that
 * nobody made -- the measure pass writing frame geometry -- and two writes
 * racing for the same version number.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const pushed: { ops: unknown[]; version: number; actor?: string }[] = [];
let reply: (value: unknown) => void = () => {};

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    applyOps: vi.fn(async (id: string, ops: unknown[], version: number, actor?: string) => {
      pushed.push({ ops, version, actor });
      return {
        id,
        version: version + 1,
        hash: 'h',
        doc: DOC,
        applied: [],
        rejected: [],
      };
    }),
    reverseHistory: vi.fn(
      () =>
        new Promise((resolve) => {
          reply = resolve;
        })
    ),
  };
});

import { reverseHistory } from '@/lib/api';
import { useStudio } from '@/store/studio';

const DOC = {
  schema_version: 2,
  template: 'plain',
  layout: 'stack',
  scaffold: false,
  unverified: [],
  personal: { name: 'Alex Morgan', email: 'a@example.com' },
  summary: { nid: 'sum_00001', text: 'one', style: 'plain' },
  experience: [],
  education: [],
  projects: [],
  skills: [],
  custom: [],
  sections: [],
  blocks: [],
  pages: [],
} as never;

const geometry = { op: 'set_geometry', nid: 'frm_aaaaa', y: 40 } as never;
const typing = { op: 'set_text', nid: 'sum_00001', value: 'two' } as never;

beforeEach(() => {
  pushed.length = 0;
  vi.clearAllMocks();
  useStudio.setState({
    documentId: 'doc-1',
    doc: DOC,
    serverDoc: DOC,
    version: 4,
    local: [],
    pending: [],
    derived: false,
    saving: false,
    error: null,
  });
});

describe('what a batch says it is', () => {
  it('a gesture is a gesture', async () => {
    await useStudio.getState().edit([typing]);
    expect(pushed[0].actor).toBe('user');
  });

  it('the measure pass says it derived what it is sending', async () => {
    // The whole fix. Filed as somebody's work it became an undo unit, and the
    // first Ctrl+Z after any edit moved geometry nobody could see.
    await useStudio.getState().relayout([geometry]);
    expect(pushed[0].actor).toBe('layout');
  });

  it('a batch carrying a real gesture is undoable, whatever else is in it', async () => {
    // Conservative on purpose: a batch a person could want back must never be
    // filed as derived, and the two can only be told apart by who staged them.
    useStudio.getState().stage([geometry], true);
    useStudio.getState().stage([typing]);
    await useStudio.getState().flush();

    expect(pushed).toHaveLength(1);
    expect(pushed[0].actor).toBe('user');
  });

  it('the next batch is judged on its own', async () => {
    await useStudio.getState().relayout([geometry]);
    await useStudio.getState().edit([typing]);
    expect(pushed.map((entry) => entry.actor)).toEqual(['layout', 'user']);
  });
});

describe('undo against a write already in flight', () => {
  it('does nothing while a batch is being sent', async () => {
    // Both write, and the two replies race to set `version`. The loser leaves
    // the client holding a stale one, so the next keystroke conflicts.
    useStudio.setState({ saving: true });
    await useStudio.getState().history('undo');
    expect(reverseHistory).not.toHaveBeenCalled();
  });

  it('sends what is staged before reversing anything', async () => {
    // `history` clears `local`. Pressed a moment after typing, it threw the
    // typing away and then reversed something older -- so the edit the person
    // meant to undo was never on the server to be undone.
    useStudio.getState().stage([typing]);
    const running = useStudio.getState().history('undo');

    await vi.waitFor(() => expect(reverseHistory).toHaveBeenCalled());
    // The staged edit went first, so the version undo reverses is the one the
    // person just made rather than whatever came before it.
    expect(pushed).toHaveLength(1);
    expect(pushed[0].actor).toBe('user');

    reply(null);
    await running;
    expect(useStudio.getState().local).toEqual([]);
  });
});
