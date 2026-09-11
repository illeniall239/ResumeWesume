import { afterEach, describe, expect, it } from 'vitest';

import { useStudio } from '@/store/studio';

/**
 * The export must not race the save.
 *
 * A PDF is rendered from the server's stored copy, and a manual inline edit
 * only reaches the server when its field commits -- an async POST on blur. The
 * export button used to fetch the render the instant it was clicked, so an
 * edit typed a moment earlier was still in flight and missing from the file.
 * `settle()` is what the button now awaits: it resolves only once nothing is
 * staged or in flight.
 */

afterEach(() => {
  useStudio.setState({ saving: false, pending: [], local: [] });
});

describe('settle', () => {
  it('resolves immediately when nothing is in flight', async () => {
    useStudio.setState({ saving: false, pending: [], local: [] });
    await expect(useStudio.getState().settle()).resolves.toBeUndefined();
  });

  it('waits until a save in flight finishes', async () => {
    useStudio.setState({ saving: true, pending: [], local: [] });

    let settled = false;
    const p = useStudio.getState().settle().then(() => (settled = true));

    // Give it a couple of frames: while saving, it must not resolve.
    await new Promise((r) => setTimeout(r, 30));
    expect(settled).toBe(false);

    // The save lands.
    useStudio.setState({ saving: false, pending: [], local: [] });
    await p;
    expect(settled).toBe(true);
  });

  it('does not resolve while edits are still staged', async () => {
    useStudio.setState({ saving: false, pending: [], local: [{ op: 'set_text' } as never] });

    let settled = false;
    void useStudio.getState().settle().then(() => (settled = true));
    await new Promise((r) => setTimeout(r, 30));
    expect(settled).toBe(false);

    useStudio.setState({ local: [] });
    await new Promise((r) => setTimeout(r, 10));
    expect(settled).toBe(true);
  });

  it('gives up after a cap so a failed save cannot wedge the export', async () => {
    // A save that errors leaves its ops in `local`; without a cap this would
    // never resolve and the export button would spin forever.
    useStudio.setState({ saving: false, pending: [], local: [{ op: 'set_text' } as never] });
    await expect(useStudio.getState().settle()).resolves.toBeUndefined();
  }, 6000);
});
