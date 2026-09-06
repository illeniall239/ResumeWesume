import type { Metadata } from 'next';
import { DM_Sans, Source_Serif_4 } from 'next/font/google';

import './globals.css';
// After globals, deliberately: globals owns the document and the PDF, and the
// board overrides only what belongs to the screen. Swapping this order would
// let the app's chrome reach the exported file.
import './board.css';

/**
 * The app's face, self-hosted.
 *
 * `next/font` downloads it at build time and serves it from our own origin, so
 * there is no request to Google at runtime -- which matters for an app whose
 * whole position is that nothing leaves the machine.
 *
 * DM Sans, chosen for softness after two faces that were not soft enough. It
 * was Archivo, a grotesque -- flat-cut terminals, tight apertures, rigid
 * verticals -- and then IBM Plex Sans, which is humanist and opens the
 * apertures but keeps a squared, engineered skeleton underneath. That skeleton
 * is what still read as firm. DM Sans is geometric with genuinely round bowls
 * and very low stroke contrast, so the softness is in the shapes themselves
 * rather than in how their ends are cut.
 *
 * One face, both roles, which is the change that came with it. The legend
 * lettering -- small uppercase labels, tracked out -- used to be a condensed
 * sibling, and DM Sans has none. Set in the same face at normal width it is a
 * little less dense and reads no worse, and the alternative was keeping an
 * entire second family downloaded for label text.
 *
 * The document's own faces are untouched and live in globals.css. A résumé's
 * typography is the thing an employer reads, and it is not ours to restyle.
 */
const body = DM_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-ui-stack',
  display: 'swap',
});

/**
 * The document's serif, for the `book` template.
 *
 * Self-hosted like the others, and that is not a preference here: headless
 * Chromium renders the exported PDF from this app's own origin, so a face
 * fetched from a third party at render time would be a request leaving the
 * machine every time someone exports their résumé -- and a missing one would
 * silently change the file. Source Serif 4 is a text face rather than a
 * display one; it holds up at the 10.5pt a résumé is actually set in.
 */
const docSerif = Source_Serif_4({
  subsets: ['latin'],
  weight: ['400', '600', '700'],
  style: ['normal', 'italic'],
  variable: '--font-doc-serif',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'ResumeWesume',
  description:
    'A résumé edited as a controlled drawing: every change numbered, clouded and reversible.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${body.variable} ${docSerif.variable}`}>
      <body>{children}</body>
    </html>
  );
}
