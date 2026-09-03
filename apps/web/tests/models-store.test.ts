/**
 * The model-selection store.
 *
 * Exercised through the store rather than the picker for the reason
 * `import-store.test.ts` gives: there is no fetch mocking library here, so the
 * seam that gets tested is the one whose logic is worth testing. The parsing
 * helpers are pure and carry the subtle case (a model id containing a slash);
 * the actions are tested against a stubbed `@/lib/api`.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  fetchProviders: vi.fn(),
  fetchModels: vi.fn(),
  saveCredentials: vi.fn(),
  forgetCredentials: vi.fn(),
  selectModel: vi.fn(),
}));

import {
  fetchModels,
  fetchProviders,
  forgetCredentials,
  saveCredentials,
  selectModel,
} from '@/lib/api';
import { modelOf, providerOf, shortLabel, useModels } from '@/store/models';

const OLLAMA = {
  id: 'ollama',
  label: 'Ollama (local)',
  needs_key: false,
    ready: true,
  base_editable: true,
  note: '',
  default_api_base: 'http://localhost:11434',
  configured: false,
  hint: '',
  api_base: null,
};

const OPENAI = { ...OLLAMA, id: 'openai', label: 'OpenAI', needs_key: true,
    ready: true, base_editable: false };

function reset() {
  useModels.setState({
    providers: [],
    selection: null,
    fallback: '',
    effective: '',
    fallbackReason: '',
    models: {},
    loadingModels: {},
    loaded: false,
    busy: false,
    error: null,
  });
  vi.clearAllMocks();
}

describe('parsing a selection', () => {
  it('splits provider from model', () => {
    expect(providerOf('ollama/qwen3:14b')).toBe('ollama');
    expect(modelOf('ollama/qwen3:14b')).toBe('qwen3:14b');
  });

  it('keeps a slash that belongs to the model id', () => {
    // Groq and OpenRouter ids are routinely vendor/model. Splitting greedily
    // would show a truncated name and store a broken id.
    expect(providerOf('groq/openai/gpt-oss-120b')).toBe('groq');
    expect(modelOf('groq/openai/gpt-oss-120b')).toBe('openai/gpt-oss-120b');
  });

  it('treats a malformed selection as nothing chosen', () => {
    expect(providerOf(null)).toBeNull();
    expect(providerOf('ollama')).toBeNull();
    expect(modelOf('/model')).toBeNull();
  });

  it('labels the control with the model, falling back to the env default', () => {
    expect(shortLabel('openai/gpt-4o', 'ollama/qwen3')).toBe('gpt-4o');
    expect(shortLabel(null, 'ollama/qwen3:14b-16k')).toBe('qwen3:14b-16k');
  });
});

describe('loading', () => {
  beforeEach(reset);

  it('takes the catalogue as given', async () => {
    vi.mocked(fetchProviders).mockResolvedValue({
      providers: [OLLAMA, OPENAI],
      selection: 'ollama/qwen3:14b-16k',
      fallback: 'ollama/qwen3:14b-16k',
      effective: 'ollama/qwen3:14b-16k',
      fallback_reason: '',
    });

    await useModels.getState().load();

    expect(useModels.getState().providers).toHaveLength(2);
    expect(useModels.getState().selection).toBe('ollama/qwen3:14b-16k');
    expect(useModels.getState().loaded).toBe(true);
  });

  it('records what will actually run, and why it differs', async () => {
    vi.mocked(fetchProviders).mockResolvedValue({
      providers: [OLLAMA, OPENAI],
      selection: 'openai/gpt-4o',
      fallback: 'ollama/qwen3:14b-16k',
      effective: 'ollama/qwen3:14b-16k',
      fallback_reason: 'OpenAI has no API key, so it cannot be used.',
    });

    await useModels.getState().load();

    expect(useModels.getState().effective).toBe('ollama/qwen3:14b-16k');
    expect(useModels.getState().fallbackReason).toContain('no API key');
  });

  it('still marks itself loaded when the catalogue fails', async () => {
    // The assistant works on whatever .env configures, so a failure here costs
    // the picker rather than the product -- it must not leave a spinner.
    vi.mocked(fetchProviders).mockRejectedValue(new Error('offline'));

    await useModels.getState().load();

    expect(useModels.getState().loaded).toBe(true);
    expect(useModels.getState().error).toBe('offline');
  });

  it('caches a model list and does not refetch it', async () => {
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'ollama',
      models: ['qwen3:14b'],
      source: 'live',
      detail: '',
    });

    await useModels.getState().loadModels('ollama');
    await useModels.getState().loadModels('ollama');

    expect(fetchModels).toHaveBeenCalledTimes(1);
  });

  it('refetches when forced', async () => {
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'ollama',
      models: [],
      source: 'live',
      detail: '',
    });

    await useModels.getState().loadModels('ollama');
    await useModels.getState().loadModels('ollama', { force: true });

    expect(fetchModels).toHaveBeenCalledTimes(2);
  });

  it('records an empty listing when the request fails', async () => {
    // Not "nothing": the menu has to render a reason rather than spin forever.
    vi.mocked(fetchModels).mockRejectedValue(new Error('502: bad gateway'));

    await useModels.getState().loadModels('openai');

    const listing = useModels.getState().models.openai;
    expect(listing.models).toEqual([]);
    expect(listing.detail).toBe('502: bad gateway');
    expect(useModels.getState().loadingModels.openai).toBe(false);
  });
});

describe('choosing a model', () => {
  beforeEach(reset);

  it('applies immediately and persists', async () => {
    vi.mocked(selectModel).mockResolvedValue(undefined);

    await useModels.getState().choose('ollama', 'qwen3:14b');

    expect(selectModel).toHaveBeenCalledWith('ollama', 'qwen3:14b');
    expect(useModels.getState().selection).toBe('ollama/qwen3:14b');
    // Accepted, so it is now what runs, and any earlier complaint is stale.
    expect(useModels.getState().effective).toBe('ollama/qwen3:14b');
    expect(useModels.getState().fallbackReason).toBe('');
  });

  it('rolls back when the server refuses', async () => {
    // The server rejects a provider with no key. The dropdown must return to
    // what is actually running rather than showing a selection that is not.
    useModels.setState({ selection: 'ollama/qwen3:14b' });
    vi.mocked(selectModel).mockRejectedValue(
      new Error('409: Add an API key for OpenAI before selecting it.')
    );

    await useModels.getState().choose('openai', 'gpt-4o');

    expect(useModels.getState().selection).toBe('ollama/qwen3:14b');
    expect(useModels.getState().error).toContain('API key');
  });
});

describe('credentials', () => {
  beforeEach(reset);

  it('replaces the provider row with what the server returned', async () => {
    useModels.setState({ providers: [OLLAMA, OPENAI] });
    vi.mocked(saveCredentials).mockResolvedValue({
      ...OPENAI,
      configured: true,
      hint: '••••9XYZ',
    });
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'openai',
      models: ['gpt-4o'],
      source: 'live',
      detail: '',
    });

    await useModels.getState().saveKey('openai', { api_key: 'sk-secret-9XYZ' });

    const openai = useModels.getState().providers.find((p) => p.id === 'openai');
    expect(openai?.configured).toBe(true);
    expect(openai?.hint).toBe('••••9XYZ');
  });

  it('never keeps the key it was given', async () => {
    useModels.setState({ providers: [OPENAI] });
    vi.mocked(saveCredentials).mockResolvedValue({ ...OPENAI, configured: true, hint: '••••9XYZ' });
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'openai',
      models: [],
      source: 'fallback',
      detail: '',
    });

    await useModels.getState().saveKey('openai', { api_key: 'sk-secret-9XYZ' });

    // The whole store, serialised: a key must not survive anywhere in it.
    expect(JSON.stringify(useModels.getState())).not.toContain('sk-secret-9XYZ');
  });

  it('refetches models after a key changes', async () => {
    // A new key means a different account, and quite possibly a live list
    // where the generic fallback was showing.
    useModels.setState({ providers: [OPENAI] });
    vi.mocked(saveCredentials).mockResolvedValue({ ...OPENAI, configured: true });
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'openai',
      models: ['gpt-4o'],
      source: 'live',
      detail: '',
    });

    await useModels.getState().saveKey('openai', { api_key: 'sk-1' });

    expect(fetchModels).toHaveBeenCalledWith('openai');
  });

  it('clears the flag when a key is removed', async () => {
    useModels.setState({
      providers: [{ ...OPENAI, configured: true, hint: '••••9XYZ' }],
    });
    vi.mocked(forgetCredentials).mockResolvedValue(undefined);
    vi.mocked(fetchModels).mockResolvedValue({
      provider: 'openai',
      models: [],
      source: 'fallback',
      detail: '',
    });

    await useModels.getState().clearKey('openai');

    const openai = useModels.getState().providers.find((p) => p.id === 'openai');
    expect(openai?.configured).toBe(false);
    expect(openai?.hint).toBe('');
  });
});
