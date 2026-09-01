"""Regenerate the PDF fixtures the ingest tests read.

Bullets are written as literal "&bull;" characters with ``list-style: none``
rather than as ordinary list markers. Chromium draws a CSS list marker as a
graphic outside the text run, so pdfminer never sees it -- a fixture built the
obvious way would quietly contain no bullet glyphs at all and would exercise
none of the glyph handling that Word and LaTeX exports actually need.

Run by hand, not by the test suite, and the output is committed:

    uv run python scripts/make_ingest_fixtures.py

Generating at test time would mean launching Chromium in CI for every run, and
would let a browser upgrade change the fixtures underneath the assertions --
turning a dependency bump into a mysterious ingest failure. Committed bytes
make the input to those tests a fixed thing, which is the only way a layout
regression can be attributed to our code.

The two layouts are chosen for what they exercise, not for coverage:

*Single column* carries right-aligned dates, the shape that makes a naive
column detector split a one-column page and detach every date from its job.

*Two column* is a genuine sidebar with enough text in it to be real, so the
same detector has to say yes here and no there.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

_STYLE = """
  @page { size: Letter; margin: 14mm; }
  body { font-family: Georgia, serif; font-size: 10.5pt; color: #000; margin: 0; }
  h1 { font-size: 20pt; margin: 0 0 2pt; letter-spacing: .5px; }
  h2 { font-size: 12pt; margin: 14pt 0 4pt; text-transform: uppercase;
       border-bottom: 1px solid #000; padding-bottom: 2pt; }
  .contact { font-size: 9.5pt; margin-bottom: 4pt; }
  .role { display: flex; justify-content: space-between; font-weight: bold; }
  .sub { display: flex; justify-content: space-between; font-style: italic;
         font-size: 10pt; }
  ul { margin: 3pt 0 8pt; padding-left: 16pt; list-style: none; }
  li { margin-bottom: 2pt; text-indent: -8pt; }
"""

SINGLE_COLUMN = f"""<!doctype html><meta charset="utf-8"><style>{_STYLE}</style>
<h1>Alex Morgan</h1>
<div class="contact">Austin, TX &middot; alex.morgan@example.com &middot;
  (512) 555-0148 &middot; linkedin.com/in/alexmorgan &middot;
  github.com/alexmorgan</div>

<h2>Summary</h2>
<p>Backend engineer with eight years building payment and ledger systems at
high transaction volume. Comfortable owning a service end to end, from schema
design through on-call, and happiest on the parts of a system where
correctness actually matters.</p>

<h2>Professional Experience</h2>
<div class="role"><span>Senior Software Engineer</span><span>Mar 2021 &ndash; Present</span></div>
<div class="sub"><span>Northwind Systems</span><span>Austin, TX</span></div>
<ul>
  <li>&bull; Rebuilt the payments ledger on an append-only model, cutting month-end
      reconciliation from four days to under three hours.</li>
  <li>&bull; Led the migration of 40 services off a shared database, running the
      cutover with no customer-visible downtime.</li>
  <li>&bull; Introduced contract tests between the billing and ledger services, which
      caught 14 breaking changes before they reached staging.</li>
</ul>
<div class="role"><span>Software Engineer</span><span>Jun 2018 &ndash; Feb 2021</span></div>
<div class="sub"><span>Contoso Retail</span><span>Dallas, TX</span></div>
<ul>
  <li>&bull; Built the inventory reservation service handling 3,000 requests per
      second at peak.</li>
  <li>&bull; Cut p99 checkout latency from 1.8s to 340ms by removing a synchronous
      call to the pricing engine.</li>
</ul>

<h2>Education</h2>
<div class="role"><span>University of Texas at Austin</span><span>2014 &ndash; 2018</span></div>
<div class="sub"><span>B.S. Computer Science</span><span>GPA 3.7</span></div>

<h2>Technical Skills</h2>
<p>Python, Go, PostgreSQL, Kafka, Redis, Docker, Kubernetes, Terraform, AWS</p>
"""

_TWO_COLUMN_STYLE = """
  @page { size: Letter; margin: 12mm; }
  body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; margin: 0; }
  h1 { font-size: 22pt; margin: 0 0 10pt; }
  h2 { font-size: 11pt; text-transform: uppercase; margin: 0 0 5pt; }
  .cols { display: flex; gap: 26mm; }
  .side { width: 46mm; }
  .main { flex: 1; }
  ul { margin: 3pt 0 10pt; padding-left: 14pt; list-style: none; }
  li { margin-bottom: 3pt; text-indent: -8pt; }
  .job { font-weight: bold; margin-top: 8pt; }
"""

TWO_COLUMN = f"""<!doctype html><meta charset="utf-8">
<style>{_TWO_COLUMN_STYLE}</style>
<h1>Priya Raman</h1>
<div class="cols">
  <div class="side">
    <h2>Contact</h2>
    <p>Seattle, WA<br>priya.raman@example.com<br>(206) 555-0173<br>
       github.com/priyaraman</p>
    <h2>Skills</h2>
    <p>TypeScript<br>React<br>Node.js<br>GraphQL<br>PostgreSQL<br>
       Playwright<br>Terraform<br>Figma</p>
    <h2>Education</h2>
    <p>University of Washington<br>B.S. Informatics<br>2015 &ndash; 2019</p>
    <h2>Languages</h2>
    <p>English<br>Tamil<br>German</p>
  </div>
  <div class="main">
    <h2>Experience</h2>
    <div class="job">Staff Frontend Engineer, Fabrikam &middot; 2022 &ndash; Present</div>
    <ul>
      <li>&bull; Owned the design system used by nine product teams, taking component
          adoption from 30% to 88% across two quarters.</li>
      <li>&bull; Cut the first-contentful-paint of the dashboard from 4.1s to 1.2s by
          moving rendering to the server and dropping three chart libraries.</li>
      <li>&bull; Set up visual regression testing that now blocks around six layout
          regressions a month before release.</li>
    </ul>
    <div class="job">Frontend Engineer, Tailspin Toys &middot; 2019 &ndash; 2022</div>
    <ul>
      <li>&bull; Rebuilt the checkout flow, lifting completion rate by 11 points.</li>
      <li>&bull; Introduced accessibility auditing to CI and brought the catalogue to
          WCAG 2.1 AA.</li>
      <li>&bull; Mentored three junior engineers, two of whom were promoted.</li>
    </ul>
    <h2>Projects</h2>
    <div class="job">Sightline &middot; 2023</div>
    <ul>
      <li>&bull; Open-source bundle visualiser, roughly 2,400 stars, used in the build
          pipelines of several mid-size teams.</li>
    </ul>
  </div>
</div>
"""

PAGES = {
    "resume_single_column.pdf": SINGLE_COLUMN,
    "resume_two_column.pdf": TWO_COLUMN,
}


async def main() -> None:
    from playwright.async_api import async_playwright

    FIXTURES.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox"])
        try:
            for name, html in PAGES.items():
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                await page.evaluate("document.fonts.ready")
                data = await page.pdf(format="Letter", print_background=True)
                (FIXTURES / name).write_bytes(data)
                await page.close()
                print(f"wrote {name} ({len(data):,} bytes)")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
