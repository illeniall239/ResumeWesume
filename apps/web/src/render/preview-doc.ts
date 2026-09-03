/**
 * The résumé the template gallery renders.
 *
 * Sized to fill a page, because the card shows a whole sheet and a résumé that
 * covered a third of one would make every template look like mostly blank
 * paper -- which is not what any of them look like in use. Two jobs, a project,
 * a degree and two skills rows is an ordinary one-page résumé and exercises
 * every element a template styles: name, tagline, contact line, section
 * heading, entry head with dates, organisation line, bullets, and both the
 * inline and stacked skills rows.
 *
 * Invented details, nobody's. `PRODUCT.md` names a seeded sample in the web app
 * as one of the fixtures that may exist; this is that, trimmed for the card.
 *
 * Ids are well-formed for the engine's own format (a kind prefix and five
 * characters of its alphabet) even though this document is never sent anywhere
 * -- a fixture that could not survive being real is a trap for whoever copies
 * it next.
 */

import type { StudioDoc } from '@/contracts/doc';

export const PREVIEW_DOC: StudioDoc = {
  schema_version: 2,
  template: 'plain',
  // The card is a rendering, not a document; the copy the gallery creates is
  // the one marked as scaffolding.
  scaffold: false,
  unverified: [],
  personal: {
    name: 'Alex Morgan',
    title: 'Senior Backend Engineer',
    email: 'alex@example.com',
    phone: '+1-555-0142',
    location: 'Austin, TX',
    website: null,
    linkedin: null,
    github: null,
  },
  summary: {
    nid: 'sum_4k2wp',
    text: 'Backend engineer with eight years building payment and data platforms at scale.',
    style: 'plain',
  },
  experience: [
    {
      nid: 'exp_7f3a2',
      title: 'Senior Backend Engineer',
      company: 'Northwind Systems',
      location: 'Austin, TX',
      years: 'Mar 2021 — Present',
      bullets: [
        {
          nid: 'blt_9c21x',
          text: 'Rebuilt the payments ledger, cutting settlement latency from four hours to nine minutes.',
          style: 'bullet',
        },
        {
          nid: 'blt_2m8qd',
          text: 'Led the migration of 40 services to async Python, reducing p99 latency by 38%.',
          style: 'bullet',
        },
        {
          nid: 'blt_7t3wf',
          text: 'Introduced contract tests across six teams, cutting integration failures by half.',
          style: 'bullet',
        },
      ],
    },
    {
      nid: 'exp_1q6zb',
      title: 'Backend Engineer',
      company: 'Cobalt Analytics',
      location: 'Remote',
      years: 'Jun 2017 — Feb 2021',
      bullets: [
        {
          nid: 'blt_4v0hs',
          text: 'Designed an ingest tier sustaining 1.2M events per second on commodity hardware.',
          style: 'bullet',
        },
        {
          nid: 'blt_8j2nk',
          text: 'Owned the query planner rewrite that took the p95 dashboard load under one second.',
          style: 'bullet',
        },
      ],
    },
  ],
  education: [
    {
      nid: 'edu_5h1nv',
      institution: 'University of Texas at Austin',
      degree: 'B.S. Computer Science',
      years: '2013 — 2017',
      detail: null,
    },
  ],
  projects: [
    {
      nid: 'prj_2r9dm',
      name: 'Ledgerline',
      role: 'Author',
      years: '2022',
      github: null,
      website: null,
      bullets: [
        {
          nid: 'blt_5z1cp',
          text: 'An open-source double-entry ledger used by three payment startups.',
          style: 'bullet',
        },
      ],
    },
  ],
  skills: [
    {
      nid: 'sgp_3d7ka',
      key: 'technicalSkills',
      label: 'Technical Skills',
      items: [
        { nid: 'skl_1p4rt', text: 'Python', source: 'original' },
        { nid: 'skl_8w2jm', text: 'Go', source: 'original' },
        { nid: 'skl_6b9zc', text: 'PostgreSQL', source: 'original' },
        { nid: 'skl_0n5vq', text: 'Kubernetes', source: 'original' },
        { nid: 'skl_3g7bx', text: 'Kafka', source: 'original' },
        { nid: 'skl_9d4fw', text: 'AWS', source: 'original' },
      ],
    },
    {
      // Long entries, so this group renders stacked rather than comma-joined
      // -- the other of the two shapes a skills row can take.
      nid: 'sgp_6y2hn',
      key: 'certificationsTraining',
      label: 'Certifications',
      items: [
        {
          nid: 'skl_2c8vk',
          text: 'AWS Certified Solutions Architect, Professional (2023)',
          source: 'original',
        },
      ],
    },
  ],
  custom: [],
  sections: [
    { key: 'summary', label: 'Summary', visible: true, order: 0 },
    { key: 'experience', label: 'Experience', visible: true, order: 1 },
    { key: 'projects', label: 'Projects', visible: true, order: 2 },
    { key: 'education', label: 'Education', visible: true, order: 3 },
    { key: 'skills', label: 'Skills', visible: true, order: 4 },
  ],
  blocks: [],
  pages: [],
  reading_order: null,
};

/**
 * What "Blank" gives you, for the card that offers it.
 *
 * A skeleton rather than an empty document. Creating one with no content at
 * all produced a page carrying nothing but the header -- every section renders
 * only when it has something in it -- so it landed on a blank sheet with
 * nothing to type into.
 *
 * Mirrors `starter_doc` in `studio/doc/schema.py`, which is what the server
 * actually builds: one job with two bullets, one degree, one skills group, and
 * an empty summary. Kept in step by hand, and worth it -- the promise the
 * gallery makes is that you get the résumé on the card, and a Blank card
 * showing anything else would break it for the one template where a person is
 * least sure what they are choosing.
 *
 * Ids are fixed here where the server mints fresh ones. Nothing is sent from
 * this fixture; the server builds its own on `starter: true`.
 */
export const BLANK_DOC: StudioDoc = {
  ...PREVIEW_DOC,
  scaffold: true,
  summary: { nid: 'sum_00000', text: '', style: 'plain' },
  experience: [
    {
      nid: 'exp_00000',
      title: '',
      company: '',
      location: '',
      years: '',
      bullets: [
        { nid: 'blt_00000', text: '', style: 'bullet' },
        { nid: 'blt_00001', text: '', style: 'bullet' },
      ],
    },
  ],
  // The starter has none, and a card showing an empty Projects heading would
  // advertise a section the document does not carry.
  projects: [],
  education: [
    { nid: 'edu_00000', institution: '', degree: '', years: '', detail: null },
  ],
  skills: [
    {
      nid: 'sgp_00000',
      key: 'technicalSkills',
      label: 'Technical Skills',
      items: [{ nid: 'skl_00000', text: '', source: 'user' }],
    },
  ],
} as StudioDoc;
