import { describe, expect, it, vi } from 'vitest';

import { readNdjsonStream, type StreamEvent } from '@/stream/ndjson';

/** Builds a Response whose body streams the given byte chunks. */
function responseOf(chunks: Uint8Array[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return new Response(stream);
}

function encode(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function event(seq: number, type: string, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ v: 1, seq, ts: '', turn_id: 't1', type, ...extra }) + '\n';
}

async function collect(chunks: Uint8Array[]) {
  const events: StreamEvent[] = [];
  const gaps: [number, number][] = [];
  const errors: string[] = [];
  let closed = false;

  await readNdjsonStream(responseOf(chunks), {
    onEvent: (e) => events.push(e),
    onGap: (expected, received) => gaps.push([expected, received]),
    onError: (error) => errors.push(error.message),
    onClose: () => {
      closed = true;
    },
  });

  return { events, gaps, errors, closed };
}

describe('NDJSON reader', () => {
  it('reads whole lines', async () => {
    const { events, closed } = await collect([
      encode(event(1, 'turn_started') + event(2, 'assistant_delta', { text: 'hi' })),
    ]);
    expect(events.map((e) => e.type)).toEqual(['turn_started', 'assistant_delta']);
    expect(closed).toBe(true);
  });

  it('reassembles an object split across chunks', async () => {
    // The failure that works in development and corrupts in production: a
    // packet boundary landing inside a JSON object.
    const line = event(1, 'assistant_delta', { text: 'hello world' });
    const half = Math.floor(line.length / 2);
    const { events } = await collect([
      encode(line.slice(0, half)),
      encode(line.slice(half)),
    ]);
    expect(events).toHaveLength(1);
    expect(events[0].text).toBe('hello world');
  });

  it('survives a chunk boundary inside a multi-byte character', async () => {
    // A split UTF-8 code point decodes to a replacement character unless the
    // decoder carries state across chunks.
    const line = event(1, 'assistant_delta', { text: 'café — naïve' });
    const bytes = encode(line);
    const cut = 30;
    const { events } = await collect([bytes.slice(0, cut), bytes.slice(cut)]);
    expect(events[0].text).toBe('café — naïve');
  });

  it('delivers one event per byte-at-a-time chunk', async () => {
    const line = event(1, 'done', { status: 'ok' });
    const chunks = Array.from(encode(line)).map((byte) => Uint8Array.of(byte));
    const { events } = await collect(chunks);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('done');
  });

  it('skips an unreadable line without losing the rest', async () => {
    const { events, errors } = await collect([
      encode(event(1, 'turn_started') + 'this is not json\n' + event(2, 'done')),
    ]);
    expect(events.map((e) => e.type)).toEqual(['turn_started', 'done']);
    expect(errors).toHaveLength(1);
  });

  it('reports a sequence gap', async () => {
    const { gaps } = await collect([encode(event(1, 'turn_started') + event(4, 'done'))]);
    expect(gaps).toEqual([[2, 4]]);
  });

  it('does not treat a heartbeat as a gap', async () => {
    // Heartbeats reuse the current seq deliberately; counting them as a gap
    // would make every idle stream look broken.
    const { gaps, events } = await collect([
      encode(event(1, 'turn_started') + event(1, 'heartbeat') + event(2, 'done')),
    ]);
    expect(gaps).toEqual([]);
    expect(events).toHaveLength(3);
  });

  it('handles a trailing line with no newline', async () => {
    const line = event(1, 'done').trimEnd();
    const { events } = await collect([encode(line)]);
    expect(events).toHaveLength(1);
  });

  it('closes cleanly on an empty body', async () => {
    const { events, closed } = await collect([]);
    expect(events).toEqual([]);
    expect(closed).toBe(true);
  });

  it('reports a missing body rather than throwing', async () => {
    const onError = vi.fn();
    const onClose = vi.fn();
    await readNdjsonStream(new Response(null), { onEvent: () => {}, onError, onClose });
    expect(onError).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
