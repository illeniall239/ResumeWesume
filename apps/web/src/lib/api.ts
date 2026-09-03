/**
 * API client.
 *
 * Every call to the backend goes through here. Requests are same-origin and
 * proxied by next.config rewrites, so there is no CORS in development and no
 * base-URL configuration to get wrong.
 */

import type {
  ApplyResponse,
  DocOp,
  DocumentResponse,
  StudioDoc,
  Template,
} from '@/contracts/doc';

const BASE = '/api/v1';

/** On the server there is no origin to be relative to. */
function url(path: string): string {
  if (typeof window === 'undefined') {
    const origin = process.env.INTERNAL_API_ORIGIN ?? 'http://127.0.0.1:8000';
    return `${origin}${BASE}${path}`;
  }
  return `${BASE}${path}`;
}

/**
 * A failed request, with its body intact.
 *
 * The status and the parsed `detail` are both kept because a 409 from the ops
 * endpoint carries `{current_version, ops_since}` — everything a client needs
 * to rebase and retry. Flattening that into a message string meant the only
 * available response to a conflict was to reload and discard the user's edit.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    message: string
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** The 409 body the ops endpoint returns. */
export interface VersionConflict {
  code: 'version_conflict';
  current_version: number;
  ops_since: DocOp[];
}

export function isVersionConflict(error: unknown): error is ApiError & {
  detail: VersionConflict;
} {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    typeof error.detail === 'object' &&
    error.detail !== null &&
    (error.detail as VersionConflict).code === 'version_conflict'
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url(path), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    cache: 'no-store',
  });

  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail ?? response.statusText;
    } catch {
      /* keep the status text */
    }
    const text = typeof detail === 'string' ? detail : JSON.stringify(detail);
    throw new ApiError(response.status, detail, `${response.status}: ${text}`);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchDocument(id: string): Promise<DocumentResponse> {
  return request<DocumentResponse>(`/documents/${id}`);
}

export interface StoredRevisions {
  /** How many assistant turns have changed this document, ever. */
  revision: number;
  turn_id: string | null;
  marks: { nid: string; mark: number; before: string | null }[];
}

/**
 * The revision number and the marks of the latest issue.
 *
 * Rebuilt server-side from the op log, because the browser's copy died with
 * the tab: the conversation came back on reload and the record of what changed
 * did not, which is the wrong way round -- one is a dialogue, the other
 * describes the artifact.
 */
export function fetchRevisions(id: string): Promise<StoredRevisions> {
  return request<StoredRevisions>(`/documents/${id}/revisions`);
}

export interface StoredMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  status: 'ok' | 'partial' | 'failed' | 'cancelled' | null;
}

/**
 * The conversation for a document, oldest first.
 *
 * Server-side rather than in the browser because the next turn is built from
 * it: a chat held only in memory meant a page reload silently emptied the
 * history sent to the model, and the assistant would ask again for facts it had
 * already been given.
 */
export function fetchMessages(id: string): Promise<{ messages: StoredMessage[] }> {
  return request<{ messages: StoredMessage[] }>(`/documents/${id}/messages`);
}

/**
 * Delete a résumé and everything under it.
 *
 * There is no undo for this one. Undo reverses a batch *within* a document; a
 * deleted document has no op log left to reverse, so the caller asks first.
 */
/**
 * Give a document a different name.
 *
 * `PATCH`, carrying no version and taking no `If-Match`: a title is *about*
 * the document rather than in it, so it does not move `version` and every open
 * editor's compare-and-set base stays valid.
 */
export function renameDocument(id: string, title: string): Promise<DocumentResponse> {
  return request<DocumentResponse>(`/documents/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

export function deleteDocument(id: string): Promise<void> {
  return request<void>(`/documents/${id}`, { method: 'DELETE' });
}

export function clearMessages(id: string): Promise<void> {
  return request<void>(`/documents/${id}/messages`, { method: 'DELETE' });
}

export function listDocuments(): Promise<DocumentResponse[]> {
  return request<DocumentResponse[]>('/documents');
}

export function createDocument(body: {
  title?: string;
  doc?: StudioDoc;
  resume_data?: Record<string, unknown>;
  /** The text an import was parsed from, when this came from an upload. */
  source_markdown?: string;
  /** How the résumé is set. Omitted leaves the server's default. */
  template?: Template;
  /** Start from a skeleton -- headings and one empty entry per section. */
  starter?: boolean;
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

/**
 * Reverse the last committed batch, or put it back.
 *
 * One `POST /ops` is one version is one undo unit, so this works identically
 * for a direct edit and for an agent turn. Resolves to `null` when there is
 * nothing to reverse — the server answers 409 for an empty stack, which is an
 * ordinary state and not worth throwing over.
 */
export async function reverseHistory(
  id: string,
  direction: 'undo' | 'redo'
): Promise<(DocumentResponse & { reversed_version: number }) | null> {
  try {
    return await request<DocumentResponse & { reversed_version: number }>(
      `/documents/${id}/${direction}`,
      { method: 'POST' }
    );
  } catch (error) {
    if ((error as Error).message.startsWith('409')) return null;
    throw error;
  }
}

/**
 * Accept text the assistant invented while the document was a template.
 *
 * An empty list means all of it. Confirming clears the marks and ends the
 * scaffolding; it changes no words, so the document's hash is unchanged and a
 * client holding one keeps it.
 */
export function confirmInvented(id: string, nids: string[] = []): Promise<DocumentResponse> {
  return request<DocumentResponse>(`/documents/${id}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ nids }),
  });
}

export function pdfUrl(id: string, template = 'ats', pageSize = 'A4'): string {
  return `${BASE}/documents/${id}/pdf?template=${template}&pageSize=${pageSize}`;
}

export interface UploadedAsset {
  id: string;
  url: string;
  mime: string;
  width: number;
  height: number;
  byte_size: number;
}

/**
 * Upload an image and get back the id a document can place.
 *
 * Sent as multipart rather than base64 JSON: a photo would grow by a third on
 * the wire, and it would land in the same request pipeline as document ops.
 * The response carries pixel dimensions so the caller can place the image at
 * its true aspect ratio without decoding the file a second time.
 *
 * `fetch` sets the multipart boundary itself, which is why this bypasses
 * `request()` and its JSON content type.
 */
export async function uploadAsset(
  file: File,
  documentId?: string
): Promise<UploadedAsset> {
  const body = new FormData();
  body.append('file', file);
  if (documentId) body.append('document_id', documentId);

  const response = await fetch(url('/assets'), { method: 'POST', body, cache: 'no-store' });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail ?? response.statusText;
    } catch {
      /* keep the status text */
    }
    const text = typeof detail === 'string' ? detail : JSON.stringify(detail);
    throw new ApiError(response.status, detail, text);
  }
  return (await response.json()) as UploadedAsset;
}

// --- model providers -------------------------------------------------------
//
// No function here returns an API key, because no endpoint does. A key is
// written with `saveCredentials` and thereafter exists to this client only as
// `configured` plus a four-character hint -- which is enough to recognise which
// key is installed and useless to anything that intercepts it.

export interface ProviderInfo {
  id: string;
  label: string;
  needs_key: boolean;
  /** Whether a turn could run on this provider right now, decided server-side. */
  ready: boolean;
  base_editable: boolean;
  note: string;
  default_api_base: string | null;
  configured: boolean;
  /** Last four characters of the stored key, masked. Never the key. */
  hint: string;
  api_base: string | null;
}

export interface ProviderCatalog {
  providers: ProviderInfo[];
  /** "provider/model", or null when .env's default is in force. */
  selection: string | null;
  /** What runs when nothing is selected. */
  fallback: string;
  /**
   * What the next turn will actually use.
   *
   * Not the same as `selection` when the selection cannot run — a provider
   * whose key was removed, say. The picker labels itself from this, so the
   * screen cannot claim a model the server has already decided against.
   */
  effective: string;
  /** Why the selection was not honoured. Empty when it was. */
  fallback_reason: string;
}

export interface ModelsResponse {
  provider: string;
  models: string[];
  /** "live" = the provider answered; "fallback" = litellm's registry. */
  source: 'live' | 'fallback';
  detail: string;
}

export function fetchProviders(): Promise<ProviderCatalog> {
  return request<ProviderCatalog>('/providers');
}

export function fetchModels(provider: string): Promise<ModelsResponse> {
  return request<ModelsResponse>(`/providers/${provider}/models`);
}

/**
 * Write a key and/or a base URL.
 *
 * `apiKey: undefined` leaves the stored key untouched, which is what lets the
 * base URL be edited without asking for a key the UI is not allowed to show.
 * An empty string clears it.
 */
export function saveCredentials(
  provider: string,
  body: { api_key?: string; api_base?: string }
): Promise<ProviderInfo> {
  return request<ProviderInfo>(`/providers/${provider}/credentials`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function forgetCredentials(provider: string): Promise<void> {
  return request<void>(`/providers/${provider}/credentials`, { method: 'DELETE' });
}

export function selectModel(provider: string, model: string): Promise<void> {
  return request<void>('/providers/selection', {
    method: 'PUT',
    body: JSON.stringify({ provider, model }),
  });
}

export interface ProviderTest {
  healthy: boolean;
  degraded?: boolean;
  model?: string;
  output?: string;
  error?: string;
  note?: string;
}

/** One real completion, so a bad key fails here rather than mid-turn. */
export function testProvider(
  provider: string,
  body: { api_key?: string; api_base?: string; model?: string } = {}
): Promise<ProviderTest> {
  return request<ProviderTest>(`/providers/${provider}/test`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
