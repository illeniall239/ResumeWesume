/**
 * API client.
 *
 * Every call to the backend goes through here. Requests are same-origin and
 * proxied by next.config rewrites, so there is no CORS in development and no
 * base-URL configuration to get wrong.
 */

import type {
  ApplyResponse,
  CanvasResponse,
  DocOp,
  DocumentResponse,
  Layout,
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

/**
 * A 404 from the API, as opposed to a network failure or anything else.
 *
 * Used where "there is nothing here" is a real answer to act on rather than a
 * fault to report: opening a link that names a board rather than the canvas it
 * sits on, for one.
 */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
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
  /**
   * The snapshot this turn can be put back to, where that means anything.
   *
   * Joined server-side on `turn_id`, so "undo this turn" survives a refresh —
   * which is exactly what somebody does when they are unsure whether an edit
   * landed. Null on a turn that changed nothing, and on every user message.
   */
  checkpoint?: string | null;
  /**
   * Every version this turn touched, and where each stood before it.
   *
   * A turn can move between versions, so putting one back means putting back
   * every board it reached rather than the last it happened to be on.
   */
  checkpoints?: { board_id: string; checkpoint_id: string }[];
  /**
   * Where to put every board back to, for a turn that is currently undone.
   *
   * A revert records the state it replaced as its own inverse, so the way back
   * is already in the log and this only reports it. Without it a reload
   * offered to undo a turn that had already been undone, and taking the offer
   * put the document back where it already was.
   */
  redo?: { board_id: string; checkpoint_id: string }[];
  /** Whether that turn is undone as the document currently stands. */
  reverted?: boolean;
  /**
   * What the assistant was working through, as one block.
   *
   * Stored with the turn rather than rebuilt here: a reload used to leave every
   * past turn as a bare paragraph, so the record of *how* the résumé came to
   * say what it says survived only until the tab was refreshed.
   */
  thinking?: string | null;
  /**
   * What its tools did — one entry per call, in the order they ran, each with
   * the status it finished in.
   *
   * Null, not empty, on a turn stored before this existed: an old transcript is
   * silent about its tools rather than claiming there were none.
   */
  activity?:
    | {
        call_id: string;
        name: string;
        tier: string;
        status: 'running' | 'applied' | 'rejected' | 'confirm' | 'done' | 'note';
        label?: string;
        detail?: string;
        code?: string;
        touched?: string[];
      }[]
    | null;
  /** Which version this turn acted on, and what it is called. */
  board_id?: string | null;
  board?: string | null;
}

/**
 * Everything said about a résumé, oldest first, across its versions.
 *
 * Server-side rather than in the browser because the next turn is built from
 * it: a chat held only in memory meant a page reload silently emptied the
 * history sent to the model, and the assistant would ask again for facts it had
 * already been given.
 *
 * Canvas-wide rather than per board, because a conversation is: you ask for a
 * version aimed at one job, read it back, then ask for another. Held per board
 * it split into as many transcripts as there were versions, and switching
 * versions silently changed the subject.
 */
export function fetchCanvasMessages(
  canvasId: string
): Promise<{ messages: StoredMessage[] }> {
  return request<{ messages: StoredMessage[] }>(`/canvases/${canvasId}/messages`);
}

export function clearCanvasMessages(canvasId: string): Promise<void> {
  return request<void>(`/canvases/${canvasId}/messages`, { method: 'DELETE' });
}

/**
 * Delete a résumé and everything under it.
 *
 * There is no undo for this one. Undo reverses a batch *within* a document; a
 * deleted document has no op log left to reverse, so the caller asks first.
 */
/**
 * Every canvas, newest activity first, each with its boards in full.
 *
 * In full because the register renders boards for real — the same
 * `DocumentFlow` the studio and the PDF use — so a card cannot go stale
 * against the thing it opens.
 */
export function fetchCanvases(): Promise<CanvasResponse[]> {
  return request<CanvasResponse[]>('/canvases');
}

export function fetchCanvas(id: string): Promise<CanvasResponse> {
  return request<CanvasResponse>(`/canvases/${id}`);
}

export function createCanvas(title = 'Untitled'): Promise<CanvasResponse> {
  return request<CanvasResponse>('/canvases', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export function renameCanvas(id: string, title: string): Promise<CanvasResponse> {
  return request<CanvasResponse>(`/canvases/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

/**
 * Delete a canvas and every board on it.
 *
 * The boards go with it: they are versions of one résumé, and keeping them
 * would leave a set of sheets with nothing in common and no way back to each
 * other.
 */
export function deleteCanvas(id: string): Promise<void> {
  return request<void>(`/canvases/${id}`, { method: 'DELETE' });
}

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
  /** How the page is arranged. A different layer from `template`: this places
   *  the frames, that styles what is inside one. */
  layout?: Layout;
  /** Start from a skeleton -- headings and one empty entry per section. */
  starter?: boolean;
  /** Put this board on an existing canvas. Omitted, it gets one of its own. */
  canvas_id?: string;
}): Promise<DocumentResponse> {
  return request<DocumentResponse>('/documents', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function applyOps(
  id: string,
  ops: DocOp[],
  version: number,
  /**
   * Whether this batch is a gesture or a re-derivation.
   *
   * Only undo bookkeeping reads it. `layout` is the measure pass correcting
   * frame geometry to match what the browser rendered -- not something a
   * person did, and not something Ctrl+Z should land on.
   */
  actor: 'user' | 'layout' = 'user'
): Promise<ApplyResponse> {
  return request<ApplyResponse>(`/documents/${id}/ops`, {
    method: 'POST',
    // The version travels as an ETag so a stale write is refused rather than
    // silently clobbering whatever landed in between.
    headers: { 'If-Match': `W/"${version}-"` },
    body: JSON.stringify({ ops, actor }),
  });
}

/**
 * Reverse the last committed batch, or put it back.
 *
 * One `POST /ops` is one version is one undo unit. That is a batch, *not* a
 * turn: the agent loop calls `apply` once per tool call, so a turn that made
 * fourteen edits is fourteen versions and fourteen presses of this. Undoing a
 * whole turn is `revertToCheckpoint` below.
 *
 * Resolves to `null` when there is nothing to reverse — the server answers 409
 * for an empty stack, which is an ordinary state and not worth throwing over.
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
 * Put the document back to how it was before an agent turn.
 *
 * The loop takes a snapshot before its first mutation and streams the id on
 * `done`, which is the only thing in this app that knows where a turn began:
 * ops are recorded per tool call, so by the time a turn ends nothing else can
 * say which of the last fourteen versions was the one you asked for.
 *
 * Restores as a *new* version rather than rewinding, so a client holding an
 * old ETag still gets a conflict instead of silently appearing current.
 */
/** A revert, and the checkpoint that undoes the revert. */
export type RevertResponse = DocumentResponse & { redo_checkpoint: string };

export function revertToCheckpoint(
  id: string,
  checkpointId: string
): Promise<RevertResponse> {
  return request<RevertResponse>(`/documents/${id}/revert`, {
    method: 'POST',
    body: JSON.stringify({ checkpoint_id: checkpointId }),
  });
}

/**
 * Aim this résumé at a job posting, or stop aiming it at one.
 *
 * `PUT`, carrying no version and taking no `If-Match`, for the same reason a
 * rename does: the posting is *about* the document rather than in it. It moves
 * neither the version nor the content hash, so pasting one hands no conflict
 * to an open editor and leaves nothing in the undo stack between two real
 * edits.
 *
 * An empty string clears it. There is no separate remove call, because "aimed
 * at nothing" is not a different kind of state.
 */
export function setJobDescription(id: string, text: string): Promise<DocumentResponse> {
  return request<DocumentResponse>(`/documents/${id}/job-description`, {
    method: 'PUT',
    body: JSON.stringify({ text }),
  });
}

/**
 * The same, from a PDF downloaded off a job board.
 *
 * Its own `fetch` rather than `request()`, which sets a JSON content type the
 * browser must be left to fill in with the multipart boundary.
 */
export async function setJobDescriptionFromPdf(
  id: string,
  file: File
): Promise<DocumentResponse> {
  const body = new FormData();
  body.append('file', file);

  const response = await fetch(url(`/documents/${id}/job-description/pdf`), {
    method: 'POST',
    body,
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
    throw new ApiError(response.status, detail, text);
  }
  return (await response.json()) as DocumentResponse;
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

/**
 * The route a browser prints, and the one the PDF export renders.
 *
 * One address for both, so the page that comes out of a printer and the file
 * that comes out of Export are the same document rather than two renderings
 * kept in step by hand. Relative, because the frame that loads it must be
 * same-origin for `print()` to reach into it.
 */
export function printUrl(id: string): string {
  return `/print/${id}`;
}

export function pdfUrl(id: string, pageSize = 'A4'): string {
  return `${BASE}/documents/${id}/pdf?pageSize=${pageSize}`;
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
