import DocumentFlow from '@/render/document-flow';
import { fetchDocument } from '@/lib/api';

/**
 * The print target.
 *
 * A server component, fetched directly from the API, with no providers and no
 * client JavaScript. Headless Chromium navigates here and waits for
 * `[data-print-root]`, which DocumentFlow carries.
 *
 * It renders the *same* DocumentFlow as the studio, so what you export is what
 * you saw. Keeping those on separate code paths is what made screen-versus-PDF
 * divergence a recurring bug in the previous app.
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

  return (
    <div className="page">
      <DocumentFlow doc={doc} />
    </div>
  );
}
