/**
 * NDJSON stream reader.
 *
 * The one subtlety that makes this worth its own module: a chunk boundary can
 * fall anywhere, including the middle of a JSON object or even a multi-byte
 * UTF-8 character. A reader that parses each chunk independently works in
 * development, where responses arrive whole, and corrupts the stream in
 * production the first time a packet splits.
 *
 * So: decode with `stream: true` so the decoder carries partial code points
 * across chunks, and buffer until a newline before parsing.
 */

export interface StreamEvent {
  v: number;
  seq: number;
  ts: string;
  turn_id: string;
  type: string;
  [key: string]: unknown;
}

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void;
  /** A gap in seq means events were lost; the caller should resync. */
  onGap?: (expected: number, received: number) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export interface StreamHandle {
  /** Aborts the fetch. Does not cancel the turn server-side. */
  abort: () => void;
  turnId: Promise<string>;
  done: Promise<void>;
}

export function readNdjsonStream(
  response: Response,
  handlers: StreamHandlers,
  startSeq = 0
): Promise<void> {
  const body = response.body;
  if (!body) {
    handlers.onError?.(new Error('The response carried no body.'));
    handlers.onClose?.();
    return Promise.resolve();
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let lastSeq = startSeq;

  const emit = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    let event: StreamEvent;
    try {
      event = JSON.parse(trimmed) as StreamEvent;
    } catch {
      // One unparseable line must not poison the rest of the stream.
      handlers.onError?.(new Error(`Skipped an unreadable event: ${trimmed.slice(0, 80)}`));
      return;
    }

    // Heartbeats deliberately reuse the current seq, so they are not a gap.
    if (event.type !== 'heartbeat') {
      if (event.seq > lastSeq + 1) handlers.onGap?.(lastSeq + 1, event.seq);
      lastSeq = Math.max(lastSeq, event.seq);
    }

    handlers.onEvent(event);
  };

  return (async () => {
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        // stream: true keeps a split multi-byte character intact.
        buffer += decoder.decode(value, { stream: true });

        let newline = buffer.indexOf('\n');
        while (newline !== -1) {
          emit(buffer.slice(0, newline));
          buffer = buffer.slice(newline + 1);
          newline = buffer.indexOf('\n');
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) emit(buffer);
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        handlers.onError?.(error as Error);
      }
    } finally {
      handlers.onClose?.();
    }
  })();
}

export function startTurn(
  body: {
    document_id: string;
    message: string;
    job_description?: string;
    history?: { role: string; content: string }[];
    busy_nids?: string[];
    consent_tokens?: string[];
  },
  handlers: StreamHandlers
): StreamHandle {
  const controller = new AbortController();
  let resolveTurnId: (id: string) => void = () => {};
  const turnId = new Promise<string>((resolve) => {
    resolveTurnId = resolve;
  });

  const done = (async () => {
    try {
      const response = await fetch('/api/v1/turns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        const detail = await response.text().catch(() => response.statusText);
        throw new Error(`${response.status}: ${detail.slice(0, 200)}`);
      }

      resolveTurnId(response.headers.get('X-Turn-Id') ?? '');
      await readNdjsonStream(response, handlers);
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        handlers.onError?.(error as Error);
      }
      handlers.onClose?.();
    }
  })();

  return { abort: () => controller.abort(), turnId, done };
}

/** Reattach to a turn after a dropped connection. */
export async function resumeTurn(
  turnId: string,
  fromSeq: number,
  handlers: StreamHandlers
): Promise<void> {
  const response = await fetch(`/api/v1/turns/${turnId}/stream?from_seq=${fromSeq}`);
  if (!response.ok) {
    // The turn is gone rather than merely finished, so waiting for more events
    // would hang forever. The caller should reload the document.
    throw new Error('turn_not_found');
  }
  await readNdjsonStream(response, handlers, fromSeq);
}

export function cancelTurn(turnId: string): Promise<Response> {
  return fetch(`/api/v1/turns/${turnId}`, { method: 'DELETE' });
}

/**
 * Upload a resume and stream its parse back.
 *
 * Lives here beside `startTurn` because it needs the same thing that function
 * does: a POST whose response is a stream, which the JSON client in `lib/api`
 * cannot express.
 *
 * The `Content-Type` header is deliberately absent. With a `FormData` body the
 * browser has to set it itself, because only the browser knows the multipart
 * boundary it generated; setting it by hand produces a boundary-less header
 * and a 400 from the server that reads exactly like a server bug.
 */
export function startImport(file: File, handlers: StreamHandlers): StreamHandle {
  const controller = new AbortController();
  let resolveImportId: (id: string) => void = () => {};
  const turnId = new Promise<string>((resolve) => {
    resolveImportId = resolve;
  });

  const form = new FormData();
  form.append('file', file);

  const done = (async () => {
    try {
      const response = await fetch('/api/v1/imports', {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });

      if (!response.ok) {
        // The upload was refused outright (too large, not a PDF). That detail
        // is the whole error, so surface it rather than a status code.
        let detail = response.statusText;
        try {
          detail = (await response.json()).detail ?? detail;
        } catch {
          /* keep the status text */
        }
        throw new Error(detail);
      }

      resolveImportId(response.headers.get('X-Import-Id') ?? '');
      await readNdjsonStream(response, handlers);
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        handlers.onError?.(error as Error);
      }
      handlers.onClose?.();
    }
  })();

  return { abort: () => controller.abort(), turnId, done };
}

export function cancelImport(importId: string): Promise<Response> {
  return fetch(`/api/v1/imports/${importId}`, { method: 'DELETE' });
}
