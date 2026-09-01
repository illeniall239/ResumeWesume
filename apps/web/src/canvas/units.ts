/**
 * The one place points become pixels.
 *
 * Geometry is stored in points because the PDF is the artifact of record and
 * `page.pdf()` reasons in inches. The browser needs CSS pixels. At the 96dpi
 * the CSS `px` unit assumes, that conversion is an exact 4:3 with no rounding
 * to accumulate — which is the reason for choosing points over millimetres,
 * where the ratio is irrational and every conversion loses a little.
 *
 * Kept to a single constant in a single file on purpose. The page-measurement
 * code this replaces had `mmToPx` in one module and `PAGE_HEIGHT_MM` plus
 * `SHEET_GAP_PX` in another, and a disagreement between them was invisible
 * until it showed up as a misplaced page break.
 */

/** CSS pixels per PostScript point. Exactly 4/3. */
export const PT_TO_PX = 96 / 72;

export function ptToPx(pt: number): number {
  return pt * PT_TO_PX;
}

export function pxToPt(px: number): number {
  return px / PT_TO_PX;
}

export interface PageSpec {
  width: number;
  height: number;
  margin: number;
}

/** A4 and Letter in points, with the margin `render_pdf` passes to Chromium. */
export const PAGE_SIZES: Record<string, PageSpec> = {
  A4: { width: 595.276, height: 841.89, margin: 28.35 },
  Letter: { width: 612, height: 792, margin: 28.35 },
};

export function pageSpec(
  size: string | undefined,
  orientation: string | undefined = 'portrait'
): PageSpec {
  const base = PAGE_SIZES[size ?? 'A4'] ?? PAGE_SIZES.A4;
  return orientation === 'landscape'
    ? { width: base.height, height: base.width, margin: base.margin }
    : base;
}

/** Space drawn between sheets on screen. Never part of the exported page. */
export const SHEET_GAP_PX = 24;
