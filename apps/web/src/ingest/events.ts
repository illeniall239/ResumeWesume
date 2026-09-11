/**
 * The import half of the NDJSON protocol.
 *
 * Separate from `@/contracts/doc` because it describes the wire, not the
 * document: these events exist only while an import is running and have no
 * Pydantic model to mirror. The turn events are typed the same way, in
 * `stream/ndjson.ts`.
 */

import type { StreamEvent } from '@/stream/ndjson';

export type SectionStatus = 'pending' | 'running' | 'parsed' | 'failed' | 'skipped';

export type FailureCode =
  | 'no_json'
  | 'invalid_shape'
  | 'timeout'
  | 'provider_error'
  | 'empty';

export interface ImportStartedEvent extends StreamEvent {
  type: 'import_started';
  filename: string;
  pages: number;
  chars: number;
  columns: number;
  warnings: string[];
}

export interface SectionFoundEvent extends StreamEvent {
  type: 'section_found';
  key: string;
  heading: string;
  chars: number;
  order: number;
  needs_model: boolean;
}

export interface SectionParsedEvent extends StreamEvent {
  type: 'section_parsed';
  key: string;
  data: Record<string, unknown>;
  source_text: string;
  ms: number;
}

export interface SectionFailedEvent extends StreamEvent {
  type: 'section_failed';
  key: string;
  code: FailureCode;
  message: string;
  source_text: string;
}

export interface ImportReadyEvent extends StreamEvent {
  type: 'import_ready';
  title: string;
  resume_data: Record<string, unknown>;
  doc: Record<string, unknown>;
  source_text: string;
  parsed: number;
  failed: number;
}

/** Human labels for the sections we know about. */
export const SECTION_LABELS: Record<string, string> = {
  contact: 'Contact details',
  summary: 'Summary',
  experience: 'Work experience',
  education: 'Education',
  projects: 'Projects',
  skills: 'Skills',
  certifications: 'Certifications',
  awards: 'Awards',
  languages: 'Languages',
  other: 'Not imported',
};

export function labelFor(key: string, heading = ''): string {
  return SECTION_LABELS[key] ?? heading ?? key;
}
