'use client';

import { useEffect, useState } from 'react';

import type { DocumentResponse } from '@/contracts/doc';
import { createDocument, listDocuments } from '@/lib/api';

const SAMPLE = {
  personalInfo: {
    name: 'Alex Morgan',
    title: 'Senior Backend Engineer',
    email: 'alex.morgan@example.com',
    phone: '+1-555-0142',
    location: 'Austin, TX',
    linkedin: 'linkedin.com/in/alexmorgan',
  },
  summary:
    'Backend engineer with eight years building payment and data platforms at scale.',
  workExperience: [
    {
      title: 'Senior Backend Engineer',
      company: 'Northwind Systems',
      location: 'Austin, TX',
      years: 'Mar 2021 - Present',
      description: [
        'Rebuilt the payments ledger, cutting settlement latency from 4h to 9 minutes.',
        'Led the migration of 40 services to async Python, reducing p99 latency 38%.',
      ],
      descriptionStyles: ['bullet', 'bullet'],
    },
    {
      title: 'Backend Engineer',
      company: 'Cobalt Analytics',
      location: 'Remote',
      years: 'Jun 2017 - Feb 2021',
      description: ['Designed an ingest tier sustaining 1.2M events/sec.'],
      descriptionStyles: ['bullet'],
    },
  ],
  education: [
    {
      institution: 'University of Texas at Austin',
      degree: 'B.S. Computer Science',
      years: '2013 - 2017',
      description: '',
    },
  ],
  additional: {
    technicalSkills: ['Python', 'Go', 'PostgreSQL', 'Kafka', 'Kubernetes', 'AWS'],
    certificationsTraining: [],
    languages: [],
    awards: [],
  },
};

export default function Home() {
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDocuments()
      .then(setDocuments)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  async function create(seeded: boolean) {
    setBusy(true);
    setError(null);
    try {
      const created = await createDocument(
        seeded
          ? { title: 'Alex Morgan (sample)', resume_data: SAMPLE }
          : { title: 'Untitled resume' }
      );
      window.location.href = `/studio/${created.id}`;
    } catch (cause) {
      setError((cause as Error).message);
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 720, margin: '0 auto', padding: '48px 24px' }}>
      <h1 style={{ marginBottom: 4 }}>ResumeResume</h1>
      <p style={{ color: '#374151', marginTop: 0 }}>
        A chat sidebar beside a live document. The assistant lands in P1; the
        document engine underneath it is already working.
      </p>

      {error && <div className="notice">{error}</div>}

      <div style={{ display: 'flex', gap: 12, margin: '24px 0' }}>
        <button className="button" onClick={() => create(true)} disabled={busy}>
          New from sample
        </button>
        <button className="button" onClick={() => create(false)} disabled={busy}>
          New blank
        </button>
      </div>

      <h2 style={{ fontSize: 16 }}>Documents</h2>
      {documents.length === 0 && <p style={{ color: '#6b7280' }}>Nothing yet.</p>}
      <ul style={{ paddingLeft: 18 }}>
        {documents.map((document) => (
          <li key={document.id} style={{ marginBottom: 6 }}>
            <a href={`/studio/${document.id}`}>{document.title}</a>{' '}
            <span className="badge">v{document.version}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
