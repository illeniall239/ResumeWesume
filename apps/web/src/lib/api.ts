/**
 * API client.
 *
 * Every call to the backend goes through here. Requests are same-origin and
 * proxied by next.config rewrites, so there is no CORS in development and no
 * base-URL configuration to get wrong.
 */

import type { ApplyResponse, DocOp, DocumentResponse, StudioDoc } from '@/contracts/doc';

const BASE = '/api/v1';

/** On the server there is no origin to be relative to. */
function url(path: string): string {
  if (typeof window === 'undefined') {
    const origin = process.env.INTERNAL_API_ORIGIN ?? 'http://127.0.0.1:8000';
    return `${origin}${BASE}${path}`;
  }
  return `${BASE}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url(path), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    cache: 'no-store',
  });

  if (!response.ok) {
    // Surface the status so callers can distinguish a conflict from a failure.
    let detail = '';
    try {
      detail = JSON.stringify((await response.json()).detail ?? '');
    } catch {
      detail = response.statusText;
    }
    throw new Error(`${response.status}: ${detail}`);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchDocument(id: string): Promise<DocumentResponse> {
  return request<DocumentResponse>(`/documents/${id}`);
}

export function listDocuments(): Promise<DocumentResponse[]> {
  return request<DocumentResponse[]>('/documents');
}

export function createDocument(body: {
  title?: string;
  doc?: StudioDoc;
  resume_data?: Record<string, unknown>;
}): Promise<DocumentResponse> {
  return request<DocumentResponse>('/documents', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function applyOps(
  id: string,
  ops: DocOp[],
  version: number
): Promise<ApplyResponse> {
  return request<ApplyResponse>(`/documents/${id}/ops`, {
    method: 'POST',
    // The version travels as an ETag so a stale write is refused rather than
    // silently clobbering whatever landed in between.
    headers: { 'If-Match': `W/"${version}-"` },
    body: JSON.stringify({ ops }),
  });
}

export function pdfUrl(id: string, template = 'ats', pageSize = 'A4'): string {
  return `${BASE}/documents/${id}/pdf?template=${template}&pageSize=${pageSize}`;
}
