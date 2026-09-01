'use client';

/**
 * The review route.
 *
 * A wrapper, because the component it renders has to live under `src/` to be
 * reachable from a test: `vitest.config.ts` aliases only `@ -> ./src`.
 */

import ImportReview from '@/ingest/review';

export default function ImportPage() {
  return <ImportReview />;
}
