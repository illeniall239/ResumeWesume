/**
 * Choosing which model the assistant runs.
 *
 * One job: pick a model. Installing keys happens in `settings/provider-settings`,
 * reached from the footer here — a password field inside a menu that closes on
 * an outside click is a good way to lose a half-pasted key.
 *
 * **Positioned `fixed`, from the button's rect.** The obvious `position:
 * absolute` inside the trigger does not work here: the chat sidebar is an
 * `overflow-y: auto` pane, and an element with a non-`visible` overflow on one
 * axis computes the other axis to `auto` as well — so the pane clips on both,
 * and the first version of this panel was trimmed at the sidebar edge. Fixed
 * positioning takes the panel out of that ancestor entirely, and the trade is
 * that it must be closed when anything scrolls, since it no longer travels with
 * its anchor.
 *
 * Models are listed under the providers that can actually run them. A provider
 * with no key shows one row saying so rather than a list you cannot pick from.
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import ProviderSettings from '@/settings/provider-settings';
import { Caret, Caution, Check } from '@/ui/marks';
import { describe, modelOf, providerOf, useModels } from '@/store/models';
import type { ProviderInfo } from '@/lib/api';

/** Panel width in px. Narrow enough for the 320px sidebar minimum. */
const PANEL_WIDTH = 288;
const GUTTER = 8;
/** Between the button and the panel. */
const GAP = 6;
/** The tallest the list ever wants to be; `.picker__panel` caps it too. */
const PANEL_MAX = 420;

/**
 * Where the panel hangs.
 *
 * One of `top` or `bottom`, never both: opening downward is anchored by its
 * top, opening upward by its bottom, so a short list grows from the button in
 * either direction instead of floating a gap away from it.
 */
interface Anchor {
  top?: number;
  bottom?: number;
  left: number;
  maxHeight: number;
}

/**
 * What to show for a model id.
 *
 * Only the subscription provider needs this. Its list carries a sentinel
 * meaning "whatever your plan picks", and rendered raw it read as a model
 * called "default" -- which told the user nothing about what would actually
 * run. The turn stream reports the model it resolved to once the turn starts.
 */
function modelLabel(model: string): string {
  return model === 'default' ? 'Plan default (Claude Code chooses)' : model;
}

function ProviderGroup({
  provider,
  onNeedsKey,
}: {
  provider: ProviderInfo;
  onNeedsKey: () => void;
}) {
  const listing = useModels((state) => state.models[provider.id]);
  const loading = useModels((state) => state.loadingModels[provider.id]);
  const choose = useModels((state) => state.choose);
  const selection = useModels((state) => state.selection);

  const active = providerOf(selection) === provider.id ? modelOf(selection) : null;

  if (provider.needs_key && !provider.configured) {
    return (
      <div className="picker__group">
        <div className="picker__heading">{provider.label}</div>
        <button type="button" className="picker__setup" onClick={onNeedsKey}>
          Add an API key to use this
        </button>
      </div>
    );
  }

  return (
    <div className="picker__group">
      <div className="picker__heading">
        {provider.label}
        {listing?.source === 'fallback' && (
          <span className="picker__hint" title={listing.detail}>
            generic list
          </span>
        )}
      </div>

      {loading && !listing && <p className="picker__empty">Loading…</p>}

      {listing && !listing.models.length && (
        <p className="picker__empty">{listing.detail || 'No models.'}</p>
      )}

      {listing?.models.map((model) => (
        <button
          key={model}
          type="button"
          className={`picker__model${model === active ? ' picker__model--active' : ''}`}
          onClick={() => choose(provider.id, model)}
        >
          <span className="picker__model-name">{modelLabel(model)}</span>
          {model === active && <Check size={13} />}
        </button>
      ))}
    </div>
  );
}

export function ModelPicker() {
  const load = useModels((state) => state.load);
  const loadModels = useModels((state) => state.loadModels);
  const loaded = useModels((state) => state.loaded);
  const providers = useModels((state) => state.providers);
  const selection = useModels((state) => state.selection);
  const fallback = useModels((state) => state.fallback);
  const effective = useModels((state) => state.effective);
  const fallbackReason = useModels((state) => state.fallbackReason);

  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState(false);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void load();
  }, [load]);

  const close = useCallback(() => setOpen(false), []);

  /**
   * Put the panel against the button, wherever the button currently is.
   *
   * Below it by preference, above it when below is the tighter side. The
   * picker used to live in a header at the top of the column, where downward
   * was always right; it now sits at the foot of the composer, and a panel
   * that only ever opened downward hung 413px below the bottom of the screen
   * with no way to reach it.
   *
   * Returns false when there is nothing to attach to — the anchor has been
   * scrolled out of the viewport — which is the only case where the panel
   * genuinely has to give up and close.
   */
  const place = useCallback((): boolean => {
    const rect = button.current?.getBoundingClientRect();
    if (!rect) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return false;

    const below = window.innerHeight - rect.bottom - GAP - GUTTER;
    const above = rect.top - GAP - GUTTER;
    const budget = Math.min(PANEL_MAX, window.innerHeight * 0.7);
    // Downward unless upward is genuinely roomier -- so a button with plenty
    // beneath it keeps the ordinary behaviour, and only one pinned near the
    // bottom flips.
    const up = below < Math.min(budget, above);

    setAnchor({
      ...(up
        ? { bottom: window.innerHeight - rect.top + GAP }
        : { top: rect.bottom + GAP }),
      // Clamped so a panel opened near the right edge stays on screen.
      left: Math.max(
        GUTTER,
        Math.min(rect.left, window.innerWidth - PANEL_WIDTH - GUTTER)
      ),
      maxHeight: Math.max(0, Math.min(budget, up ? above : below)),
    });
    return true;
  }, []);

  const toggle = () => {
    if (open) {
      close();
      return;
    }
    place();
    setOpen(true);

    // Only the providers that can actually run something. Asking an
    // unconfigured cloud provider for models costs a round trip to be told
    // what the catalogue already said.
    for (const provider of providers) {
      if (!provider.needs_key || provider.configured) void loadModels(provider.id);
    }
  };

  useEffect(() => {
    if (!open) return;

    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || button.current?.contains(target)) return;
      close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    /**
     * Keep up with a moving anchor, rather than closing.
     *
     * The panel is `fixed`, so it does not travel with the button and would be
     * left stranded mid-air if something scrolled it away. The first version
     * answered that by closing on any scroll at all, which was worse than the
     * problem: scrolling the model list *is* a scroll, so the menu shut the
     * moment you tried to read it. Scrolling the transcript closed it too,
     * despite the button not having moved a pixel.
     *
     * So: a scroll inside the panel is ignored outright, and everything else
     * re-places the panel under wherever the button now is. Closing is left
     * for the one case that cannot be repositioned — the anchor scrolled off
     * screen entirely.
     *
     * Captured, because scroll does not bubble: without `true` a scroll on the
     * pane never reaches a window listener at all.
     */
    const onScroll = (event: Event) => {
      // `instanceof Node`, not a truthiness check: a scroll can be targeted at
      // `window`, and `Node.contains` *throws* on a non-Node argument rather
      // than returning false. An exception here would kill the handler and
      // strand the panel for the rest of the session.
      const target = event.target;
      if (target instanceof Node && panel.current?.contains(target)) return;
      if (!place()) close();
    };
    const onResize = () => {
      if (!place()) close();
    };

    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  }, [open, close, place]);

  const openSettings = () => {
    close();
    setSettings(true);
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        className="picker__button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={
          !loaded
            ? 'Finding out which model will answer'
            : fallbackReason
              ? `${fallbackReason} Running ${describe(effective, providers)} instead.`
              : `Running ${describe(effective, providers)}`
        }
      >
        {/* Labelled from `effective`, never from `selection` or `fallback`: a
            selection the server has rejected must not show as live, and the
            .env fallback is not what runs when a Claude login is present. The
            tooltip used to say "Using ollama/... (from .env)" over turns that
            were answering on the subscription. */}
        <span className="picker__current">
          {loaded ? describe(effective, providers) : 'Loading…'}
        </span>
        {fallbackReason && (
          <span className="picker__warn" title={fallbackReason}>
            <Caution size={13} />
          </span>
        )}
        <Caret size={12} />
      </button>

      {open && anchor && (
        <div
          ref={panel}
          className="picker__panel"
          role="listbox"
          aria-label="Choose a model"
          style={{
            top: anchor.top,
            bottom: anchor.bottom,
            left: anchor.left,
            width: PANEL_WIDTH,
            maxHeight: anchor.maxHeight,
          }}
        >
          <div className="picker__scroll">
            {providers.map((provider) => (
              <ProviderGroup
                key={provider.id}
                provider={provider}
                onNeedsKey={openSettings}
              />
            ))}
          </div>

          {fallbackReason && (
            <p className="picker__notice">
              {fallbackReason} Running <code>{effective}</code> instead.
            </p>
          )}

          <button type="button" className="picker__manage" onClick={openSettings}>
            Manage providers and keys…
          </button>
        </div>
      )}

      {settings && <ProviderSettings onClose={() => setSettings(false)} />}
    </>
  );
}

export default ModelPicker;
