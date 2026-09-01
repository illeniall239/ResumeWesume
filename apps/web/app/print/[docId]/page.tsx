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
 * Two templates share this route. `?template=ats` renders the content subtree
 * through DocumentFlow -- the plain single column, geometry ignored -- and
 * anything else renders the placed canvas. Both are the same components the
 * studio uses, because screen-versus-PDF divergence was a recurring bug in the
 * previous app whenever those were separate code paths.
 */
export const dynamic = 'force-dynamic';

export default async function PrintPage({
  params,
  searchParams,
}: {
  params: Promise<{ docId: string }>;
  searchParams: Promise<{ template?: string }>;
}) {
  const { docId } = await params;
  const { template } = await searchParams;

  let doc = null;
  try {
    doc = (await fetchDocument(docId)).doc;
  } catch {
    // Render an empty print root rather than throwing: Chromium waits for the
    // selector, and a thrown error would hang the export until its timeout
    // instead of failing fast with an empty page.
    return <div data-print-root />;
  }

  // The ATS export ignores layout entirely and renders the content subtree
  // through the flowing renderer. Nothing is reverse-engineered from boxes:
  // it is the same component, on the same content, that produced every PDF
  // this app has ever exported.
  if (template === 'ats' || !doc.pages.length) {
    return (
      <div className="page">
        <DocumentFlow doc={doc} />
      </div>
    );
  }

  return <PageCanvas doc={doc} />;
}
