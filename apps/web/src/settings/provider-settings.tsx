/**
 * Where API keys are installed.
 *
 * A dialog of its own rather than a section of the model dropdown. Picking a
 * model is a thing you do often and want over with; installing a key is a thing
 * you do once, with a value pasted from another window, and it needs room to
 * explain itself. Putting a password field inside a menu that closes on an
 * outside click also meant a half-typed key vanished if you clicked the wrong
 * pixel.
 *
 * Rendered `position: fixed` against the viewport. The chat sidebar is an
 * `overflow-y: auto` pane, which clips on both axes, so anything absolutely
 * positioned inside it is trimmed at the pane edge -- which is exactly what was
 * wrong with the first version of this UI.
 *
 * **No key is ever displayed.** The server does not return one; a stored key is
 * only ever `configured` plus four characters. What is typed here lives in this
 * component's state until it is saved, then is dropped.
 */

'use client';

import { useEffect, useState } from 'react';

import { testProvider, type ProviderInfo, type ProviderTest } from '@/lib/api';
import { useModels } from '@/store/models';

function ProviderRow({ provider }: { provider: ProviderInfo }) {
  const saveKey = useModels((state) => state.saveKey);
  const clearKey = useModels((state) => state.clearKey);
  const busy = useModels((state) => state.busy);

  const [draftKey, setDraftKey] = useState('');
  const [draftBase, setDraftBase] = useState(provider.api_base ?? '');
  const [result, setResult] = useState<ProviderTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setDraftBase(provider.api_base ?? '');
  }, [provider.api_base]);

  const dirty = draftKey.length > 0 || draftBase !== (provider.api_base ?? '');

  const save = async () => {
    await saveKey(provider.id, {
      // Undefined rather than "" when untouched: an empty string is the
      // instruction to clear the stored key, so editing only a URL must not
      // send one.
      api_key: draftKey || undefined,
      api_base: provider.base_editable ? draftBase : undefined,
    });
    setDraftKey('');
    setResult(null);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  const check = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(
        await testProvider(provider.id, {
          api_key: draftKey || undefined,
          api_base: provider.base_editable ? draftBase || undefined : undefined,
        })
      );
    } catch (error) {
      setResult({ healthy: false, error: (error as Error).message });
    } finally {
      setTesting(false);
    }
  };

  // Decided by the server. "No key needed" used to be the same question as
  // "usable", and stopped being when a provider arrived that needs no key and
  // is still unusable until you have signed in to Claude Code on this machine.
  const ready = provider.ready;

  return (
    <section className="provider-row" data-provider={provider.id}>
      <header className="provider-row__head">
        <h3 className="provider-row__name">{provider.label}</h3>
        <span
          className={`provider-row__state provider-row__state--${
            ready ? 'ready' : 'missing'
          }`}
        >
          {provider.needs_key
            ? provider.configured
              ? `key ${provider.hint}`
              : 'no key'
            : ready
              ? 'ready'
              : 'not signed in'}
        </span>
      </header>

      {provider.note && <p className="provider-row__note">{provider.note}</p>}

      <div className="provider-row__fields">
        {provider.needs_key && (
          <label className="field">
            <span className="legend field__legend">API key</span>
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={draftKey}
              onChange={(event) => setDraftKey(event.target.value)}
              placeholder={provider.configured ? 'replace the stored key' : 'paste your key'}
            />
          </label>
        )}

        {provider.base_editable && (
          <label className="field">
            <span className="legend field__legend">Server URL</span>
            <input
              type="text"
              spellCheck={false}
              value={draftBase}
              onChange={(event) => setDraftBase(event.target.value)}
              placeholder={provider.default_api_base ?? ''}
            />
          </label>
        )}
      </div>

      <div className="provider-row__actions">
        {/* Only where there is something to save. The Claude subscription has
            no key and no address -- it uses the login already on this machine
            -- so it offered a Save that could never enable and a Remove key
            for a key that does not exist, which `clearKey` would have gone
            looking for. */}
        {(provider.needs_key || provider.base_editable) && (
          <button
            className="ctl ctl--small"
            type="button"
            onClick={save}
            disabled={busy || !dirty}
          >
            {saved ? 'Saved' : 'Save'}
          </button>
        )}
        <button
          className="ctl ctl--small"
          type="button"
          onClick={check}
          disabled={testing || (provider.needs_key && !draftKey && !provider.configured)}
        >
          {testing ? 'Testing…' : 'Test'}
        </button>
        {provider.needs_key && provider.configured && (
          <button
            className="link"
            type="button"
            onClick={() => clearKey(provider.id)}
            disabled={busy}
          >
            Remove key
          </button>
        )}
      </div>

      {result && (
        <p
          className={`provider-row__result provider-row__result--${
            result.healthy ? 'ok' : 'bad'
          }`}
        >
          {result.healthy
            ? `Reached ${result.model ?? 'the model'}.${
                result.degraded
                  ? ' It returned no text, which is normal for a reasoning model.'
                  : ''
              }`
            : result.error || 'Could not reach the provider.'}
        </p>
      )}
    </section>
  );
}

export function ProviderSettings({ onClose }: { onClose: () => void }) {
  const providers = useModels((state) => state.providers);
  const error = useModels((state) => state.error);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="scrim"
      // Only a press on the backdrop itself, never one that bubbled up from the
      // dialog: without the target check, releasing a drag-select inside a text
      // field closed the dialog and lost what was typed.
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="settings"
        role="dialog"
        aria-modal="true"
        aria-label="Model providers"
      >
        <header className="settings__head">
          <h2 className="settings__title">Model providers</h2>
          <span className="rail__spacer" />
          <button className="link" type="button" onClick={onClose}>
            Close
          </button>
        </header>

        <p className="settings__intro">
          Keys are stored on this machine and sent only to the provider they
          belong to. They are never shown again once saved; the app keeps only
          the last four characters so you can tell which key is installed.
        </p>

        {error && <p className="settings__error">{error}</p>}

        <div className="settings__body">
          {providers.map((provider) => (
            <ProviderRow key={provider.id} provider={provider} />
          ))}
        </div>

      </div>
    </div>
  );
}

export default ProviderSettings;
