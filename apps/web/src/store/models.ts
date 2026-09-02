/**
 * Which model the assistant uses, and the keys that make one reachable.
 *
 * A fourth store beside `useStudio`, `useChat` and `useImport`, on the same
 * reasoning: opening a dropdown and listing forty models must not re-render the
 * resume or the transcript. It is also the store with the longest-lived data --
 * the selection outlives the document you happen to have open.
 *
 * **No API key is ever held here.** The server never returns one, so there is
 * nothing to hold: a provider is `configured` with a four-character `hint`, and
 * the draft key a user is typing lives in the settings component's own state
 * until it is saved, then is dropped. A key in a module-scoped store would sit
 * in memory for the session and show up in any state dump.
 *
 * Models are cached per provider for the life of the page. The list changes
 * when someone pulls a model or edits a key, and both of those go through
 * actions here that invalidate it -- so a refetch on every menu open would
 * spend a round trip to learn nothing.
 */

'use client';

import { create } from 'zustand';

import {
  fetchModels,
  fetchProviders,
  forgetCredentials,
  saveCredentials,
  selectModel,
  type ModelsResponse,
  type ProviderCatalog,
  type ProviderInfo,
} from '@/lib/api';

export interface ModelsState {
  providers: ProviderInfo[];
  /** "provider/model", or null while .env's default is in force. */
  selection: string | null;
  /** What runs when nothing is selected, for the UI to name. */
  fallback: string;
  /** What the next turn will actually use. The label comes from this. */
  effective: string;
  /** Why the selection is not what is running. Empty when it is. */
  fallbackReason: string;

  /** Model lists, by provider id. Absent means "not fetched yet". */
  models: Record<string, ModelsResponse>;
  /** Providers whose list is in flight, so the menu can say so. */
  loadingModels: Record<string, boolean>;

  loaded: boolean;
  busy: boolean;
  error: string | null;

  load: () => Promise<void>;
  loadModels: (provider: string, options?: { force?: boolean }) => Promise<void>;
  choose: (provider: string, model: string) => Promise<void>;
  saveKey: (provider: string, body: { api_key?: string; api_base?: string }) => Promise<void>;
  clearKey: (provider: string) => Promise<void>;
  clearError: () => void;
}

/** The provider half of "provider/model", split once from the left. */
export function providerOf(selection: string | null): string | null {
  if (!selection) return null;
  const cut = selection.indexOf('/');
  return cut > 0 ? selection.slice(0, cut) : null;
}

/**
 * The model half.
 *
 * Split once, not on every slash: a model id routinely contains one of its own
 * (`openai/gpt-oss-120b` on Groq, and most OpenRouter ids), so splitting
 * greedily would show a truncated name in the picker and store a broken id.
 */
export function modelOf(selection: string | null): string | null {
  if (!selection) return null;
  const cut = selection.indexOf('/');
  return cut > 0 ? selection.slice(cut + 1) : null;
}

/** How a selection reads in a one-line control: the model, not the pair. */
export function shortLabel(selection: string | null, fallback: string): string {
  const model = modelOf(selection ?? fallback);
  return model ?? fallback;
}

export const useModels = create<ModelsState>((set, get) => ({
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

  async load() {
    try {
      const catalog: ProviderCatalog = await fetchProviders();
      set({
        providers: catalog.providers,
        selection: catalog.selection,
        fallback: catalog.fallback,
        effective: catalog.effective,
        fallbackReason: catalog.fallback_reason,
        loaded: true,
      });
    } catch (error) {
      // Not fatal, and deliberately not surfaced as a blocking error: the
      // assistant still works on whatever .env configures, so a failure here
      // costs the picker, not the product.
      set({ loaded: true, error: (error as Error).message });
    }
  },

  async loadModels(provider, options) {
    if (!options?.force && get().models[provider]) return;
    set((state) => ({ loadingModels: { ...state.loadingModels, [provider]: true } }));
    try {
      const response = await fetchModels(provider);
      set((state) => ({ models: { ...state.models, [provider]: response } }));
    } catch (error) {
      // Record an empty listing rather than nothing, so the menu renders "could
      // not read the model list" instead of an indefinite spinner.
      set((state) => ({
        models: {
          ...state.models,
          [provider]: {
            provider,
            models: [],
            source: 'fallback',
            detail: (error as Error).message,
          },
        },
      }));
    } finally {
      set((state) => ({ loadingModels: { ...state.loadingModels, [provider]: false } }));
    }
  },

  async choose(provider, model) {
    const previous = get().selection;
    // Optimistic: the picker closes on the click, and a dropdown that snaps
    // back a beat later reads as a bug rather than as a rejection.
    set({ selection: `${provider}/${model}`, busy: true, error: null });
    try {
      await selectModel(provider, model);
      // The server accepted it, so it is now what runs -- and any earlier
      // complaint about an unusable selection no longer applies.
      set({ busy: false, effective: `${provider}/${model}`, fallbackReason: '' });
    } catch (error) {
      set({ selection: previous, busy: false, error: (error as Error).message });
    }
  },

  async saveKey(provider, body) {
    set({ busy: true, error: null });
    try {
      const updated = await saveCredentials(provider, body);
      set((state) => ({
        providers: state.providers.map((item) =>
          item.id === provider ? updated : item
        ),
        busy: false,
      }));
      // A new key means a different account and therefore a different model
      // list -- and quite possibly a live one where the fallback was showing.
      await get().loadModels(provider, { force: true });
    } catch (error) {
      set({ busy: false, error: (error as Error).message });
    }
  },

  async clearKey(provider) {
    set({ busy: true, error: null });
    try {
      await forgetCredentials(provider);
      set((state) => ({
        providers: state.providers.map((item) =>
          item.id === provider
            ? { ...item, configured: false, hint: '', api_base: null }
            : item
        ),
        busy: false,
      }));
      await get().loadModels(provider, { force: true });
    } catch (error) {
      set({ busy: false, error: (error as Error).message });
    }
  },

  clearError() {
    set({ error: null });
  },
}));
