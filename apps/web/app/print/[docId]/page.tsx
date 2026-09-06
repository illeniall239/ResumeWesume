import { PageCanvas } from '@/canvas/page-canvas';
import DocumentFlow from '@/render/document-flow';
import { fetchDocument } from '@/lib/api';

/**
 * The print target.
 *
 * A server component, fetched directly from the API, with no providers and no
 * client JavaScript. Headless Chromium navigates here and waits for
 * `[data-print-root]`, which DocumentFlow carries.
 *
 * It renders the placed canvas -- the same `PageCanvas` the studio draws, so
 * the file is the document rather than a second rendering of it that has to be
 * kept in step. Screen-versus-PDF divergence was a recurring bug in the
 * previous app whenever those were separate code paths.
 *
 * A document with no pages still has to produce something, and the flowing
 * renderer is what it produced before the canvas existed. There used to be a
 * `?template=ats` that asked for that on purpose; it threw away every position
 * on the canvas, and it was the default, so the file most people got was the
 * one that did not look like their document.
 */
export const dynamic = 'force-dynamic';

export default async function PrintPage({
  params,
}: {
  params: Promise<{ docId: string }>;
}) {
  const { docId } = await params;

  let doc = null;
  try {
    doc = (await fetchDocument(docId)).doc;
  } catch {
    // Render an empty print root rather than throwing: Chromium waits for the
    // selector, and a thrown error would hang the export until its timeout
    // instead of failing fast with an empty page.
    return <div data-print-root />;
  }

  // Nothing to place it on. The same component, on the same content, that
  // produced every PDF this app exported before pages existed.
  if (!doc.pages.length) {
    return (
      <div className="page">
        <DocumentFlow doc={doc} />
      </div>
    );
  }

  return <PageCanvas doc={doc} />;
}
