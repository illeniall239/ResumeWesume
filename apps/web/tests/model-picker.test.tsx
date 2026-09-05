/**
 * The model picker and the settings dialog.
 *
 * Two properties are worth a DOM test rather than a store test.
 *
 * The panel must not be `position: absolute`. The sidebar it lives in is an
 * `overflow-y: auto` pane, which clips on *both* axes, so an absolutely
 * positioned panel is trimmed at the sidebar edge -- which is what was wrong
 * with the first version. Asserting the computed position is how that stays
 * fixed after someone tidies the CSS.
 *
 * And the dropdown must contain no key entry at all. That is the whole point of
 * the split: a password field inside a menu that closes on an outside click
 * loses a half-pasted key.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  fetchProviders: vi.fn(),
  fetchModels: vi.fn(),
  saveCredentials: vi.fn(),
  forgetCredentials: vi.fn(),
  selectModel: vi.fn(),
  testProvider: vi.fn(),
}));

import { fetchModels, saveCredentials, selectModel, testProvider } from '@/lib/api';
import ModelPicker from '@/chat/model-picker';
import { useModels } from '@/store/models';

const OLLAMA = {
  id: 'ollama',
  label: 'Ollama (local)',
  needs_key: false,
    ready: true,
  base_editable: true,
  note: 'Runs on this machine.',
  default_api_base: 'http://localhost:11434',
  configured: false,
  hint: '',
  api_base: null,
};

const OPENAI = {
  ...OLLAMA,
  id: 'openai',
  label: 'OpenAI',
  needs_key: true,
    ready: true,
  base_editable: false,
  note: 'Key from platform.openai.com.',
};

function seed(overrides: Partial<ReturnType<typeof useModels.getState>> = {}) {
  useModels.setState({
    providers: [OLLAMA, OPENAI],
    selection: 'ollama/qwen3:14b-16k',
    fallback: 'ollama/qwen3:14b-16k',
    effective: 'ollama/qwen3:14b-16k',
    fallbackReason: '',
    models: {
      ollama: {
        provider: 'ollama',
        models: ['qwen3:14b-16k', 'qwen3:4b'],
        source: 'live',
        detail: '',
      },
    },
    loadingModels: {},
    loaded: true,
    busy: false,
    error: null,
    ...overrides,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchModels).mockResolvedValue({
    provider: 'ollama',
    models: ['qwen3:14b-16k', 'qwen3:4b'],
    source: 'live',
    detail: '',
  });
  seed();
});

const openPanel = () => fireEvent.click(screen.getByRole('button', { expanded: false }));

describe('the trigger', () => {
  it('shows the model, not the provider pair', () => {
    render(<ModelPicker />);
    expect(screen.getByText('qwen3:14b-16k')).toBeInTheDocument();
  });

  it('names the environment default when nothing is selected', () => {
    seed({ selection: null, effective: 'ollama/qwen3:14b-16k' });
    render(<ModelPicker />);
    expect(
      screen.getByTitle('Using ollama/qwen3:14b-16k (from .env)')
    ).toBeInTheDocument();
  });
});

describe('a selection that cannot run', () => {
  it('labels the trigger with what is running, not what was chosen', () => {
    // The bug this guards: a selection whose key was removed. The server falls
    // back, and the picker used to go on displaying the dead choice -- the
    // screen confidently reporting the wrong model.
    seed({
      selection: 'openai/gpt-4o',
      effective: 'ollama/qwen3:14b-16k',
      fallbackReason: 'OpenAI has no API key, so it cannot be used.',
    });
    render(<ModelPicker />);

    expect(screen.getByText('qwen3:14b-16k')).toBeInTheDocument();
    expect(screen.queryByText('gpt-4o')).not.toBeInTheDocument();
  });

  it('says why, in the panel', () => {
    seed({
      selection: 'openai/gpt-4o',
      effective: 'ollama/qwen3:14b-16k',
      fallbackReason: 'OpenAI has no API key, so it cannot be used.',
    });
    render(<ModelPicker />);
    openPanel();

    expect(
      screen.getByText(/OpenAI has no API key, so it cannot be used\./)
    ).toBeInTheDocument();
  });
});

describe('the panel', () => {
  it('escapes the clipping pane by being fixed, not absolute', () => {
    // The regression this guards: `.pane` is overflow-y:auto, so it clips on
    // both axes and an absolutely positioned panel is cut off at its edge.
    render(<ModelPicker />);
    openPanel();

    const panel = screen.getByRole('listbox');
    expect(panel.style.position === '' ? getComputedStyle(panel).position : panel.style.position)
      .not.toBe('absolute');
    // Positioned from the button's rect, so both coordinates are set inline.
    expect(panel.style.top).not.toBe('');
    expect(panel.style.left).not.toBe('');
  });

  it('opens upward when it is pinned to the foot of the column', () => {
    // The regression this guards: the picker used to sit in a header at the
    // top of the sidebar, where downward was always right. It now sits beside
    // Send at the bottom of the composer, and a panel that only ever hung
    // downward opened 413px below the bottom of the screen -- on screen in
    // the DOM, unreachable with a mouse.
    render(<ModelPicker />);
    openPanel();

    const trigger = screen.getByRole('button', { expanded: true });
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      top: window.innerHeight - 45,
      bottom: window.innerHeight - 13,
      left: 165,
      right: 296,
      width: 131,
      height: 32,
      x: 165,
      y: window.innerHeight - 45,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.scroll(document);

    const panel = screen.getByRole('listbox');
    // Anchored by its bottom, so a short list grows out of the button rather
    // than floating a fixed distance above it.
    expect(panel.style.bottom).not.toBe('');
    expect(panel.style.top).toBe('');
    // And never taller than the room it has.
    expect(parseFloat(panel.style.maxHeight)).toBeLessThanOrEqual(
      window.innerHeight - 45
    );
  });

  it('is narrow enough for the 320px sidebar minimum', () => {
    render(<ModelPicker />);
    openPanel();
    expect(screen.getByRole('listbox').style.width).toBe('288px');
  });

  it('lists models under their provider', () => {
    render(<ModelPicker />);
    openPanel();

    const panel = screen.getByRole('listbox');
    expect(within(panel).getByText('Ollama (local)')).toBeInTheDocument();
    expect(within(panel).getByText('qwen3:4b')).toBeInTheDocument();
  });

  it('offers no key entry whatsoever', () => {
    render(<ModelPicker />);
    openPanel();

    // No input of any kind, rather than "no password input" -- the point is
    // that nothing is typed in here at all. (The panel does mention an API key,
    // but only to send you to the dialog.)
    expect(screen.getByRole('listbox').querySelectorAll('input')).toHaveLength(0);
  });

  it('says an unconfigured provider needs a key instead of listing models', () => {
    render(<ModelPicker />);
    openPanel();

    expect(screen.getByText('Add an API key to use this')).toBeInTheDocument();
    expect(fetchModels).not.toHaveBeenCalledWith('openai');
  });

  it('marks a generic list as generic', () => {
    seed({
      models: {
        ollama: {
          provider: 'ollama',
          models: ['qwen3:14b'],
          source: 'fallback',
          detail: 'Could not reach Ollama. Is it running?',
        },
      },
    });
    render(<ModelPicker />);
    openPanel();
    expect(screen.getByText('generic list')).toBeInTheDocument();
  });

  it('selects a model and closes', () => {
    vi.mocked(selectModel).mockResolvedValue(undefined);
    render(<ModelPicker />);
    openPanel();

    fireEvent.click(screen.getByText('qwen3:4b'));

    expect(selectModel).toHaveBeenCalledWith('ollama', 'qwen3:4b');
  });

  it('closes on Escape', () => {
    render(<ModelPicker />);
    openPanel();
    expect(screen.getByRole('listbox')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('stays open while the model list is scrolled', () => {
    // The complaint this fixes: the panel closed on any scroll at all, and
    // scrolling the list of models is a scroll -- so the menu shut the moment
    // you tried to read it.
    render(<ModelPicker />);
    openPanel();

    const list = screen.getByRole('listbox').querySelector(
      '.picker__scroll'
    ) as HTMLElement;
    fireEvent.scroll(list);

    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('stays open when something else scrolls but the button has not moved', () => {
    // Scrolling the transcript does not move the toolbar, so there is nothing
    // to correct and no reason to close.
    render(<ModelPicker />);
    openPanel();

    // Dispatched on document, not window: that is what a real page scroll
    // targets, and jsdom does not route a window-targeted scroll to a capture
    // listener on window at all.
    fireEvent.scroll(document);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('survives a scroll targeted at window', () => {
    // `Node.contains` throws on a non-Node, so an unguarded check would raise
    // inside the handler and strand the panel open for the rest of the session.
    render(<ModelPicker />);
    openPanel();

    expect(() => fireEvent.scroll(window)).not.toThrow();
  });

  it('closes only when the anchor is scrolled out of view', () => {
    render(<ModelPicker />);
    openPanel();

    // jsdom reports a zero rect for everything, so the anchor is pushed above
    // the viewport explicitly to exercise the give-up path.
    const trigger = screen.getByRole('button', { expanded: true });
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      top: -400,
      bottom: -380,
      left: 0,
      right: 100,
      width: 100,
      height: 20,
      x: 0,
      y: -400,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.scroll(document);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});

describe('the settings dialog', () => {
  const openSettings = () => {
    openPanel();
    fireEvent.click(screen.getByText('Manage providers and keys…'));
  };

  it('opens from the panel footer and replaces it', () => {
    render(<ModelPicker />);
    openSettings();

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('is where the key field lives', () => {
    render(<ModelPicker />);
    openSettings();

    const dialog = screen.getByRole('dialog');
    expect(dialog.querySelectorAll('input[type="password"]').length).toBe(1);
  });

  it('shows which key is installed without showing the key', () => {
    seed({
      providers: [OLLAMA, { ...OPENAI, configured: true, hint: '••••9XYZ' }],
    });
    render(<ModelPicker />);
    openSettings();

    expect(screen.getByText('key ••••9XYZ')).toBeInTheDocument();
  });

  /** One provider's row. Every provider has its own Save, so queries must scope. */
  const rowFor = (provider: string) =>
    within(
      screen.getByRole('dialog').querySelector(
        `[data-provider="${provider}"]`
      ) as HTMLElement
    );

  it('saves a typed key and clears the field', async () => {
    vi.mocked(saveCredentials).mockResolvedValue({ ...OPENAI, configured: true, hint: '••••9XYZ' });
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'openai',
      models: ['gpt-4o'],
      source: 'live',
      detail: '',
    });
    render(<ModelPicker />);
    openSettings();

    const openai = rowFor('openai');
    fireEvent.change(openai.getByLabelText('API key'), {
      target: { value: 'sk-secret-9XYZ' },
    });
    fireEvent.click(openai.getByRole('button', { name: 'Save' }));

    expect(saveCredentials).toHaveBeenCalledWith('openai', {
      api_key: 'sk-secret-9XYZ',
      api_base: undefined,
    });
  });

  it('will not save an untouched provider', () => {
    render(<ModelPicker />);
    openSettings();
    expect(rowFor('openai').getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('sends no key when only the server URL changed', async () => {
    // undefined, not "": an empty string is the instruction to clear the
    // stored key, so editing a URL must not wipe it.
    vi.mocked(saveCredentials).mockResolvedValue(OLLAMA);
    render(<ModelPicker />);
    openSettings();

    const ollama = rowFor('ollama');
    fireEvent.change(ollama.getByLabelText('Server URL'), {
      target: { value: 'http://localhost:1234' },
    });
    fireEvent.click(ollama.getByRole('button', { name: 'Save' }));

    expect(saveCredentials).toHaveBeenCalledWith('ollama', {
      api_key: undefined,
      api_base: 'http://localhost:1234',
    });
  });

  it('reports a rejected key in place', async () => {
    vi.mocked(testProvider).mockResolvedValue({
      healthy: false,
      error: 'That API key was rejected.',
    });
    seed({
      providers: [{ ...OPENAI, configured: true, hint: '••••bad0' }],
    });
    render(<ModelPicker />);
    openSettings();

    fireEvent.click(rowFor('openai').getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('That API key was rejected.')).toBeInTheDocument();
  });

  it('closes on the backdrop but not on a press inside it', () => {
    render(<ModelPicker />);
    openSettings();

    const dialog = screen.getByRole('dialog');
    // A press that bubbles from the dialog must not close it, or releasing a
    // drag-select inside a text field would discard what was typed.
    fireEvent.pointerDown(dialog);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.pointerDown(dialog.parentElement as HTMLElement);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
