import type { Metadata } from 'next';
import { Archivo, Archivo_Narrow, Source_Serif_4 } from 'next/font/google';

import './globals.css';
// After globals, deliberately: globals owns the document and the PDF, and the
// board overrides only what belongs to the screen. Swapping this order would
// let the app's chrome reach the exported file.
import './board.css';

/**
 * Drawing-office lettering, self-hosted.
 *
 * `next/font` downloads these at build time and serves them from our own
 * origin, so there is no request to Google at runtime -- which matters for an
 * app whose whole position is that nothing leaves the machine.
 *
 * Archivo Narrow is the legend face: condensed, upright, monoline, and legible
 * in caps at ten pixels, which is what a title block and a schedule header
 * need. Archivo is the same skeleton at normal width and carries everything
 * that is actually prose. One superfamily, two roles.
 *
 * The document's own faces are untouched and live in globals.css. A résumé's
 * typography is the thing an employer reads, and it is not ours to restyle.
 */
const narrow = Archivo_Narrow({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-narrow',
  display: 'swap',
});

const body = Archivo({
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
    <html lang="en" className={`${narrow.variable} ${body.variable} ${docSerif.variable}`}>
      <body>{children}</body>
    </html>
  );
}
