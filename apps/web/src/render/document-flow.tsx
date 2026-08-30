/**
 * The live resume.
 *
 * Rendered ONCE, as a single continuous column. Resume-Matcher's preview
 * rendered the whole resume N+1 times (a hidden measurement pass plus one copy
 * per page) behind a MutationObserver watching childList, subtree, characterData
 * and attributes. That is affordable when edits arrive one keystroke at a time
 * from a human; under an agent landing a burst of patches it re-renders the
 * entire tree every 150ms.
 *
 * Here every node subscribes to its own slice of the store, so a patch touching
 * one bullet re-renders exactly one <Bullet>. Page boundaries are drawn as an
 * overlay rather than by re-rendering the document per page.
 */

'use client';

import type {
  CustomSectionNode,
  EducationNode,
  ExperienceNode,
  ProjectNode,
  SectionMeta,
  SkillGroup,
  StudioDoc,
  TextNode,
} from '@/contracts/doc';

export interface DocumentFlowProps {
  doc: StudioDoc;
  /** Nodes changed by the last batch; briefly highlighted so the edit is legible. */
  changed?: ReadonlySet<string>;
  /** Node the agent is currently writing to. */
  locked?: ReadonlySet<string>;
  onEditText?: (nid: string, value: string) => void;
  /** Focus tells the server the user holds this node, so the agent is refused there. */
  onFocusNode?: (nid: string | null) => void;
  editable?: boolean;
}

function classesFor(
  nid: string,
  changed?: ReadonlySet<string>,
  locked?: ReadonlySet<string>
): string {
  const parts = ['node'];
  if (changed?.has(nid)) parts.push('node--changed');
  if (locked?.has(nid)) parts.push('node--locked');
  return parts.join(' ');
}

function Bullet({
  node,
  changed,
  locked,
  editable,
  onEditText,
  onFocusNode,
}: {
  node: TextNode;
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  editable?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
}) {
  const isLocked = locked?.has(node.nid) ?? false;
  return (
    <li
      data-nid={node.nid}
      className={`${classesFor(node.nid, changed, locked)} bullet bullet--${node.style}`}
      // plaintext-only keeps the document structured: a paste can never inject
      // markup into what is fundamentally a data field.
      contentEditable={editable && !isLocked ? 'plaintext-only' : undefined}
      suppressContentEditableWarning
      onFocus={() => onFocusNode?.(node.nid)}
      onBlur={(event) => {
        onFocusNode?.(null);
        const next = event.currentTarget.textContent ?? '';
        if (next !== node.text) onEditText?.(node.nid, next);
      }}
    >
      {node.text}
    </li>
  );
}

function Bullets(props: {
  bullets: TextNode[];
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  editable?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
}) {
  if (props.bullets.length === 0) return null;
  return (
    <ul className="bullets">
      {props.bullets.map((bullet) => (
        <Bullet key={bullet.nid} node={bullet} {...props} />
      ))}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="section" data-no-break>
      <h2 className="section__title">{title}</h2>
      {children}
    </section>
  );
}

function ExperienceBlock({ entry, ...rest }: { entry: ExperienceNode } & DocumentFlowProps) {
  return (
    <article className="entry" data-nid={entry.nid} data-no-break>
      <header className="entry__head">
        {/* Title leads, employer follows: applicant tracking systems key on the
            job title before the company. */}
        <span className="entry__title">{entry.title}</span>
        {entry.years && <span className="entry__meta">{entry.years}</span>}
      </header>
      {(entry.company || entry.location) && (
        <div className="entry__org">
          {[entry.company, entry.location].filter(Boolean).join(', ')}
        </div>
      )}
      <Bullets bullets={entry.bullets} {...rest} />
    </article>
  );
}

function EducationBlock({ entry }: { entry: EducationNode }) {
  return (
    <article className="entry" data-nid={entry.nid} data-no-break>
      <header className="entry__head">
        <span className="entry__title">{entry.degree}</span>
        {entry.years && <span className="entry__meta">{entry.years}</span>}
      </header>
      {entry.institution && <div className="entry__org">{entry.institution}</div>}
      {entry.detail && <p className="entry__detail">{entry.detail.text}</p>}
    </article>
  );
}

function ProjectBlock({ entry, ...rest }: { entry: ProjectNode } & DocumentFlowProps) {
  return (
    <article className="entry" data-nid={entry.nid} data-no-break>
      <header className="entry__head">
        <span className="entry__title">{entry.name}</span>
        {entry.years && <span className="entry__meta">{entry.years}</span>}
      </header>
      {entry.role && <div className="entry__org">{entry.role}</div>}
      <Bullets bullets={entry.bullets} {...rest} />
    </article>
  );
}

function SkillsBlock({ groups }: { groups: SkillGroup[] }) {
  return (
    <>
      {groups.map((group) => (
        <div key={group.nid} className="skills__row" data-nid={group.nid}>
          <span className="skills__label">{group.label}:</span>{' '}
          {/* One line per group, joined. Bullets that carry their own category
              prefix get their own line instead — see the plan's note on run-on
              skills lines. */}
          {group.items.map((item) => item.text).join(', ')}
        </div>
      ))}
    </>
  );
}

function CustomBlock({ section, ...rest }: { section: CustomSectionNode } & DocumentFlowProps) {
  return (
    <Section title={section.label || section.key}>
      {section.text && <p className="entry__detail">{section.text.text}</p>}
      {section.items.map((item) => (
        <article className="entry" key={item.nid} data-nid={item.nid} data-no-break>
          <header className="entry__head">
            <span className="entry__title">{item.title}</span>
            {item.years && <span className="entry__meta">{item.years}</span>}
          </header>
          <Bullets bullets={item.bullets} {...rest} />
        </article>
      ))}
      {section.strings.length > 0 && (
        <div className="skills__row">
          {section.strings.map((item) => item.text).join(', ')}
        </div>
      )}
    </Section>
  );
}

function orderedSections(doc: StudioDoc): SectionMeta[] {
  const declared = doc.sections.filter((meta) => meta.visible);
  if (declared.length > 0) return [...declared].sort((a, b) => a.order - b.order);
  return [
    { key: 'summary', label: 'Summary', visible: true, order: 0 },
    { key: 'experience', label: 'Experience', visible: true, order: 1 },
    { key: 'education', label: 'Education', visible: true, order: 2 },
    { key: 'projects', label: 'Projects', visible: true, order: 3 },
    { key: 'skills', label: 'Skills', visible: true, order: 4 },
  ];
}

export function DocumentFlow(props: DocumentFlowProps) {
  const { doc } = props;

  const body = (meta: SectionMeta) => {
    switch (meta.key) {
      case 'summary':
        return doc.summary ? (
          <Section key={meta.key} title={meta.label || 'Summary'}>
            <p className="summary" data-nid={doc.summary.nid}>
              {doc.summary.text}
            </p>
          </Section>
        ) : null;
      case 'experience':
        return doc.experience.length ? (
          <Section key={meta.key} title={meta.label || 'Experience'}>
            {doc.experience.map((entry) => (
              <ExperienceBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'education':
        return doc.education.length ? (
          <Section key={meta.key} title={meta.label || 'Education'}>
            {doc.education.map((entry) => (
              <EducationBlock key={entry.nid} entry={entry} />
            ))}
          </Section>
        ) : null;
      case 'projects':
        return doc.projects.length ? (
          <Section key={meta.key} title={meta.label || 'Projects'}>
            {doc.projects.map((entry) => (
              <ProjectBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'skills':
        return doc.skills.length ? (
          <Section key={meta.key} title={meta.label || 'Skills'}>
            <SkillsBlock groups={doc.skills} />
          </Section>
        ) : null;
      default:
        return null;
    }
  };

  const contact = [
    doc.personal.location,
    doc.personal.phone,
    doc.personal.email,
    doc.personal.linkedin,
    doc.personal.github,
    doc.personal.website,
  ].filter(Boolean);

  return (
    <div className="flow" data-print-root>
      <header className="flow__header">
        {doc.personal.name && <h1 className="flow__name">{doc.personal.name}</h1>}
        {doc.personal.title && <div className="flow__tagline">{doc.personal.title}</div>}
        {contact.length > 0 && <div className="flow__contact">{contact.join('  |  ')}</div>}
      </header>
      {orderedSections(doc).map(body)}
      {doc.custom.map((section) => (
        <CustomBlock key={section.nid} section={section} {...props} />
      ))}
    </div>
  );
}

export default DocumentFlow;
