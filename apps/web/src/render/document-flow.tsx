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

import { Fragment, type ReactNode } from 'react';
import type {
  CustomSectionNode,
  EducationNode,
  ExperienceNode,
  ProjectNode,
  SectionMeta,
  SkillGroup,
  StudioDoc,
  TextBlockNode,
  TextNode,
} from '@/contracts/doc';

export interface DocumentFlowProps {
  doc: StudioDoc;
  /** Nodes changed by the last batch; briefly highlighted so the edit is legible. */
  changed?: ReadonlySet<string>;
  /** Node the agent is currently writing to. */
  locked?: ReadonlySet<string>;
  /** Nodes the assistant invented while the document was scaffolding. */
  unverified?: ReadonlySet<string>;
  /**
   * Text a tool call is still writing, keyed by node id or `nid.field`.
   *
   * Threaded as a prop rather than read from the store, because this renderer
   * also draws the PDF: a half-written sentence must never reach a printed
   * page, and a component that reached for live state could not promise that.
   */
  drafts?: ReadonlyMap<string, string>;
  onEditText?: (nid: string, value: string) => void;
  /**
   * Commit an attribute of a node, addressed as ``nid.field`` (or
   * ``personal.email``).
   *
   * Most of a resume is not text nodes. A job title, an employer, a set of
   * dates, a degree, a name -- those are *fields* on an entry, and the engine
   * writes them with `set_field` rather than `set_text`. Without this, the only
   * words anyone could edit by hand were bullets, and everything else had to go
   * through the assistant.
   */
  onEditField?: (target: string, value: string) => void;
  /** Focus tells the server the user holds this node, so the agent is refused there. */
  onFocusNode?: (nid: string | null) => void;
  /**
   * Draw empty fields as their hint, even where they are not editable yet.
   *
   * The canvas makes a frame's text live only while that frame is the one
   * being edited, so that a frame still has a surface to grab. That is right,
   * and it collided with a résumé that has no words in it yet: an empty field
   * rendered nothing, so a new document showed section headings floating over
   * blank paper with nothing to aim at -- and no way to start typing, because
   * there was nothing to double-click.
   *
   * With this set, an empty field still occupies its space and shows what
   * belongs there. It stays a hint drawn by CSS from `data-placeholder`, never
   * text in the document, so a blur cannot commit it. Off by default, which is
   * what keeps it out of the print route and the import preview -- an exported
   * PDF must never say "Your name".
   */
  placeholders?: boolean;
  editable?: boolean;
  /**
   * Render only this subtree: a section key, or a content nid.
   *
   * How one component serves both the flowing ATS export and a canvas made of
   * many frames. A frame is this component pointed at what it is bound to, so
   * every renderer below, the contentEditable bullet, the change flash and the
   * `data-nid` addressing are shared rather than reimplemented per surface.
   */
  root?: string;
  /** Nids claimed by another frame, which this one must not draw twice. */
  exclude?: ReadonlySet<string>;
}

function classesFor(
  nid: string,
  changed?: ReadonlySet<string>,
  locked?: ReadonlySet<string>,
  unverified?: ReadonlySet<string>
): string {
  const parts = ['node'];
  if (changed?.has(nid)) parts.push('node--changed');
  if (locked?.has(nid)) parts.push('node--locked');
  // Written by the assistant while the document was still a template, so the
  // words are invented rather than reported. Marked until the person says
  // otherwise -- the failure this exists to prevent is someone carrying a line
  // into an interview that nobody ever checked.
  if (unverified?.has(nid)) parts.push('node--unverified');
  return parts.join(' ');
}

/**
 * Bind the shared editing props to one node, so a field is three arguments.
 *
 * Every field on an entry needs the same six props threaded to it. Passing them
 * one at a time turned each renderer into a wall of plumbing where the thing
 * being rendered was the hardest part to see.
 */
function fieldsOf(
  nid: string,
  rest: Omit<DocumentFlowProps, 'doc'>
): (field: string, value: string | null | undefined, placeholder: string) => {
  nid: string;
  field: string;
  value: string;
  placeholder: string;
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
  editable?: boolean;
  placeholders?: boolean;
  onEditField?: (target: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
} {
  return (field, value, placeholder) => ({
    nid,
    field,
    value: value ?? '',
    placeholder,
    changed: rest.changed,
    locked: rest.locked,
    unverified: rest.unverified,
    drafts: rest.drafts,
    editable: rest.editable,
    placeholders: rest.placeholders,
    onEditField: rest.onEditField,
    onFocusNode: rest.onFocusNode,
  });
}

/**
 * One editable run of words.
 *
 * The single place `contentEditable` is spelled, because every rule attached to
 * it has to hold everywhere: `plaintext-only` so a paste can never inject
 * markup into what is a data field; a node the agent is writing to is frozen; a
 * blur that changed nothing emits no op; and focus is reported so the server
 * refuses the assistant on the node under the user's cursor.
 *
 * `field` decides which op the blur produces. A text node commits `set_text`
 * against its nid; anything else -- a job title, an employer, a name -- commits
 * `set_field` against `nid.attribute`, which is how those are stored.
 */
function Editable({
  as: Tag = 'span',
  className,
  nid,
  field,
  value,
  placeholder,
  changed,
  locked,
  unverified,
  drafts,
  editable,
  placeholders,
  onEditText,
  onEditField,
  onFocusNode,
}: {
  as?: 'span' | 'div' | 'p' | 'li' | 'h1';
  className?: string;
  /** The node this belongs to: what gets locked, flashed and focus-reported. */
  nid: string;
  /** Attribute name when this is a field rather than a text node. */
  field?: string;
  value: string;
  /**
   * Shown when empty, so a blank field is still somewhere to click.
   *
   * Drawn by CSS from `data-placeholder`, never rendered as content: a
   * placeholder in the DOM is text, and the blur below reads text, so it would
   * be committed as the value the moment the user clicked in and out again.
   */
  placeholder?: string;
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
  unverified?: ReadonlySet<string>;
  editable?: boolean;
  placeholders?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onEditField?: (target: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
}) {
  const isLocked = locked?.has(nid) ?? false;
  const live = editable && !isLocked;

  // A tool call is writing here right now. Its text is shown in place of the
  // stored value so the words appear as they are typed -- and it is *only*
  // shown: `value` below is still what the document says, so a blur commits
  // the real text and a draft that never lands leaves nothing behind.
  const target = field ? `${nid}.${field}` : nid;
  const drafted = drafts?.get(target);
  const shown = drafted ?? value;
  // An empty field shows what belongs in it whenever the document is being
  // worked on, not only while this particular run happens to be live. The hint
  // is drawn by CSS from the attribute and is never text in the document.
  const hinted = live || placeholders;

  return (
    <Tag
      // Only a text node carries `data-nid`: a field is part of its entry, and
      // minting a second element with the same id would break every lookup
      // that assumes one node, one element.
      {...(field ? { 'data-field': `${nid}.${field}` } : { 'data-nid': nid })}
      className={[
        classesFor(nid, changed, locked, unverified),
        className,
        hinted ? 'editable' : null,
        drafted !== undefined ? 'node--drafting' : null,
      ]
        .filter(Boolean)
        .join(' ')}
      data-placeholder={hinted ? placeholder : undefined}
      contentEditable={live ? 'plaintext-only' : undefined}
      suppressContentEditableWarning
      onFocus={() => onFocusNode?.(nid)}
      onBlur={(event) => {
        onFocusNode?.(null);
        const next = event.currentTarget.textContent ?? '';
        if (next === value) return;
        if (field) onEditField?.(`${nid}.${field}`, next);
        else onEditText?.(nid, next);
      }}
    >
      {shown}
    </Tag>
  );
}

function Bullet({
  node,
  ...rest
}: {
  node: TextNode;
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
  editable?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
}) {
  return (
    <Editable
      as="li"
      className={`bullet bullet--${node.style}`}
      nid={node.nid}
      value={node.text}
      {...rest}
    />
  );
}

function Bullets(props: {
  bullets: TextNode[];
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
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
      <h2
        className="section__title"
        data-page-block={`title:${title}`}
        data-page-heading
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

function ExperienceBlock({ entry, ...rest }: { entry: ExperienceNode } & DocumentFlowProps) {
  const field = fieldsOf(entry.nid, rest);
  return (
    <>
    <article
      className="entry"
      data-nid={entry.nid}
      data-page-block={entry.nid}
      data-no-break
    >
      <header className="entry__head">
        {/* Title leads, employer follows: applicant tracking systems key on the
            job title before the company. */}
        <Editable className="entry__title" {...field('title', entry.title, 'Job title')} />
        {(rest.editable || rest.placeholders || entry.years) && (
          <Editable className="entry__meta" {...field('years', entry.years, 'Dates')} />
        )}
      </header>
      {/* Kept while editing even when both are blank, so there is somewhere to
          click to fill them in -- but omitted otherwise, or the export carries
          an empty line where an employer would have been. */}
      {(rest.editable || rest.placeholders || entry.company || entry.location) && (
        <div className="entry__org">
          <Editable {...field('company', entry.company, 'Company')} />
          {/* The separator is drawn whenever both sides are showing something,
              which includes two hints on a résumé that has not been filled in
              yet -- otherwise a new document reads "CompanyLocation". */}
          {(entry.company || rest.placeholders) && (entry.location || rest.placeholders)
            ? ', '
            : ''}
          <Editable {...field('location', entry.location, 'Location')} />
        </div>
      )}
      <Bullets bullets={entry.bullets} {...rest} />
    </article>
    </>
  );
}

function EducationBlock({
  entry,
  ...rest
}: { entry: EducationNode } & Omit<DocumentFlowProps, 'doc'>) {
  const field = fieldsOf(entry.nid, rest);
  return (
    <>
    <article
      className="entry"
      data-nid={entry.nid}
      data-page-block={entry.nid}
      data-no-break
    >
      <header className="entry__head">
        <Editable className="entry__title" {...field('degree', entry.degree, 'Degree')} />
        {(rest.editable || rest.placeholders || entry.years) && (
          <Editable className="entry__meta" {...field('years', entry.years, 'Dates')} />
        )}
      </header>
      {(rest.editable || rest.placeholders || entry.institution) && (
        <div className="entry__org">
          <Editable {...field('institution', entry.institution, 'Institution')} />
        </div>
      )}
      {entry.detail && (
        <Editable
          as="p"
          className="entry__detail"
          nid={entry.detail.nid}
          value={entry.detail.text}
          changed={rest.changed}
          locked={rest.locked}
          drafts={rest.drafts}
          editable={rest.editable}
          onEditText={rest.onEditText}
          onFocusNode={rest.onFocusNode}
        />
      )}
    </article>
    </>
  );
}

function ProjectBlock({ entry, ...rest }: { entry: ProjectNode } & DocumentFlowProps) {
  const field = fieldsOf(entry.nid, rest);
  return (
    <>
    <article
      className="entry"
      data-nid={entry.nid}
      data-page-block={entry.nid}
      data-no-break
    >
      <header className="entry__head">
        <Editable className="entry__title" {...field('name', entry.name, 'Project')} />
        {(rest.editable || rest.placeholders || entry.years) && (
          <Editable className="entry__meta" {...field('years', entry.years, 'Dates')} />
        )}
      </header>
      {(rest.editable || rest.placeholders || entry.role) && (
        <div className="entry__org">
          <Editable {...field('role', entry.role, 'Role')} />
        </div>
      )}
      <Bullets bullets={entry.bullets} {...rest} />
    </article>
    </>
  );
}

/**
 * Whether a group's entries have to be stacked rather than comma-joined.
 *
 * A skills group is a run of short tokens and reads best as one dense line —
 * that is also the shape an ATS parses most reliably. Certifications and awards
 * are not that: the entries are long and carry commas of their own, so joining
 * them with ", " produces a run of text where the boundary between two
 * credentials is indistinguishable from the comma inside one of them.
 */
function mustStack(group: SkillGroup): boolean {
  return group.items.some((item) => item.text.includes(',') || item.text.length > 48);
}

function SkillsBlock({
  groups,
  ...rest
}: { groups: SkillGroup[] } & Omit<DocumentFlowProps, 'doc'>) {
  const item = (skill: { nid: string; text: string }, as?: 'li') => (
    <Editable
      as={as}
      nid={skill.nid}
      value={skill.text}
      changed={rest.changed}
      locked={rest.locked}
      drafts={rest.drafts}
      editable={rest.editable}
      onEditText={rest.onEditText}
      onFocusNode={rest.onFocusNode}
    />
  );

  return (
    <>
      {groups.map((group) => {
        const field = fieldsOf(group.nid, rest);
        return mustStack(group) ? (
          <div
            key={group.nid}
            className="skills__row"
            data-nid={group.nid}
            data-page-block={group.nid}
          >
            <Editable className="skills__label" {...field('label', group.label, 'Group')} />:
            <ul className="skills__list">
              {/* The `<li>` *is* the editable, so the item keeps carrying its
                  own `data-nid` rather than handing it to a span inside. */}
              {group.items.map((skill) => (
                <Fragment key={skill.nid}>{item(skill, 'li')}</Fragment>
              ))}
            </ul>
          </div>
        ) : (
          <div
            key={group.nid}
            className="skills__row"
            data-nid={group.nid}
            data-page-block={group.nid}
          >
            <Editable className="skills__label" {...field('label', group.label, 'Group')} />:{' '}
            {/* Each skill is its own run rather than one joined string, so a
                single one can be corrected without retyping the row. */}
            {group.items.map((skill, index) => (
              <Fragment key={skill.nid}>
                {index > 0 ? ', ' : ''}
                {item(skill)}
              </Fragment>
            ))}
          </div>
        );
      })}
    </>
  );
}

function CustomBlock({ section, ...rest }: { section: CustomSectionNode } & DocumentFlowProps) {
  return (
    <Section title={section.label || section.key}>
      {section.text && (
        <Editable
          as="p"
          className="entry__detail"
          nid={section.text.nid}
          value={section.text.text}
          changed={rest.changed}
          locked={rest.locked}
          drafts={rest.drafts}
          editable={rest.editable}
          onEditText={rest.onEditText}
          onFocusNode={rest.onFocusNode}
        />
      )}
      {section.items.map((item) => {
        const field = fieldsOf(item.nid, rest);
        return (
          <Fragment key={item.nid}>
          <article
            className="entry"
            data-nid={item.nid}
            data-page-block={item.nid}
            data-no-break
          >
            <header className="entry__head">
              <Editable className="entry__title" {...field('title', item.title, 'Title')} />
              {(rest.editable || rest.placeholders || item.years) && (
                <Editable className="entry__meta" {...field('years', item.years, 'Dates')} />
              )}
            </header>
            <Bullets bullets={item.bullets} {...rest} />
          </article>
          </Fragment>
        );
      })}
      {section.strings.length > 0 && (
        <div className="skills__row">
          {section.strings.map((entry, index) => (
            <Fragment key={entry.nid}>
              {index > 0 ? ', ' : ''}
              <Editable
                nid={entry.nid}
                value={entry.text}
                changed={rest.changed}
                locked={rest.locked}
                drafts={rest.drafts}
                editable={rest.editable}
                onEditText={rest.onEditText}
                onFocusNode={rest.onFocusNode}
              />
            </Fragment>
          ))}
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
  const { doc, root, exclude } = props;

  /** Entries this frame is responsible for: not claimed by a sibling frame. */
  const mine = <T extends { nid: string }>(entries: T[]): T[] =>
    exclude?.size ? entries.filter((entry) => !exclude.has(entry.nid)) : entries;

  const body = (meta: SectionMeta) => {
    switch (meta.key) {
      case 'summary':
        return doc.summary ? (
          <Section key={meta.key} title={meta.label || 'Summary'}>
            <Editable
              as="p"
              className="summary"
              nid={doc.summary.nid}
              value={doc.summary.text}
              placeholder="A sentence or two about you"
              changed={props.changed}
              locked={props.locked}
              drafts={props.drafts}
              unverified={props.unverified}
              editable={props.editable}
              placeholders={props.placeholders}
              onEditText={props.onEditText}
              onFocusNode={props.onFocusNode}
            />
          </Section>
        ) : null;
      case 'experience':
        return mine(doc.experience).length ? (
          <Section key={meta.key} title={meta.label || 'Experience'}>
            {mine(doc.experience).map((entry) => (
              <ExperienceBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'education':
        return mine(doc.education).length ? (
          <Section key={meta.key} title={meta.label || 'Education'}>
            {mine(doc.education).map((entry) => (
              <EducationBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'projects':
        return mine(doc.projects).length ? (
          <Section key={meta.key} title={meta.label || 'Projects'}>
            {mine(doc.projects).map((entry) => (
              <ProjectBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'skills':
        return doc.skills.length ? (
          <Section key={meta.key} title={meta.label || 'Skills'}>
            <SkillsBlock groups={doc.skills} {...props} />
          </Section>
        ) : null;
      default:
        return null;
    }
  };

  // Kept as field names, not values: each part of the contact line is its own
  // editable run, so a wrong digit in a phone number is a click and a keypress
  // rather than retyping the whole line.
  const contact = (
    ['location', 'phone', 'email', 'linkedin', 'github', 'website'] as const
  ).filter((key) => doc.personal[key]);

  const personal = fieldsOf('personal', props);
  const header = (
    <header className="flow__header" data-page-block="header">
      {(props.editable || props.placeholders || doc.personal.name) && (
        <Editable as="h1" className="flow__name" {...personal('name', doc.personal.name, 'Your name')} />
      )}
      {(props.editable || props.placeholders || doc.personal.title) && (
        <Editable
          as="div"
          className="flow__tagline"
          {...personal('title', doc.personal.title, 'Your title')}
        />
      )}
      {contact.length > 0 && (
        <div className="flow__contact">
          {contact.map((key, index) => (
            <Fragment key={key}>
              {index > 0 ? '  |  ' : ''}
              <Editable {...personal(key, doc.personal[key], key)} />
            </Fragment>
          ))}
        </div>
      )}
    </header>
  );

  // A frame renders one subtree. `data-print-root` stays on the outermost
  // element either way, because headless Chromium waits for that selector and
  // a canvas page has to satisfy it too.
  // The template rides on the root element rather than on `body`, so a frame
  // on a canvas page resolves the same one the print route does and neither
  // has to be told about it.
  const flow = `flow flow--${doc.template ?? 'plain'}`;

  if (root) {
    return (
      <div className={flow} data-print-root>
        {renderRoot(root, { doc, props, body, header, mine })}
      </div>
    );
  }

  return (
    <div className={flow} data-print-root>
      {header}
      {orderedSections(doc).map(body)}
      {doc.custom.map((section) => (
        <CustomBlock key={section.nid} section={section} {...props} />
      ))}
      {/* Free text belongs in the ATS export too. The project's own rule is
          "if it has words, it has a nid" -- which makes a hand-placed note
          content, not decoration, and silently dropping it would lose real
          writing from the version most employers actually parse. Images and
          shapes carry no words and are correctly absent. */}
      {(doc.blocks ?? []).map((block) => (
        <FreeBlock key={block.nid} block={block} {...props} />
      ))}
    </div>
  );
}

/**
 * Render whatever `root` names: the header, a whole section, or one entry.
 *
 * Returning `null` for an unknown ref is deliberate. A frame bound to content
 * that no longer exists draws an empty box, which is visible and fixable; the
 * engine's coverage gate is what stops that state being reachable in the first
 * place, and a renderer that threw would take the whole page down with it.
 */
/** A free text block: words placed by hand, still addressable and editable. */
function FreeBlock({
  block,
  ...props
}: { block: TextBlockNode } & Omit<DocumentFlowProps, 'doc'>) {
  return (
    <div className={`block block--${block.role}`} data-nid={block.nid}>
      {block.lines.map((line) => (
        <Line key={line.nid} node={line} {...props} />
      ))}
    </div>
  );
}

/**
 * One editable line of a free text block.
 *
 * Shares `Bullet`'s contract deliberately -- `plaintext-only`, blur-to-commit,
 * the same `data-nid` -- so a hand-placed caption behaves exactly like a bullet
 * and the agent's `set_text` works on it without knowing the difference.
 */
function Line({
  node,
  changed,
  locked,
  drafts,
  editable,
  onEditText,
  onFocusNode,
}: {
  node: TextNode;
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
  editable?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
}) {
  return (
    <Editable
      as="p"
      className="block__line"
      nid={node.nid}
      value={node.text}
      placeholder="Text"
      changed={changed}
      locked={locked}
      drafts={drafts}
      editable={editable}
      onEditText={onEditText}
      onFocusNode={onFocusNode}
    />
  );
}

function renderRoot(
  root: string,
  ctx: {
    doc: StudioDoc;
    props: DocumentFlowProps;
    body: (meta: SectionMeta) => ReactNode;
    header: ReactNode;
    mine: <T extends { nid: string }>(entries: T[]) => T[];
  }
): ReactNode {
  const { doc, props, body, header } = ctx;

  if (root === 'personal') return header;

  if (root === 'blocks') {
    return (doc.blocks ?? []).map((block) => (
      <FreeBlock key={block.nid} block={block} {...props} />
    ));
  }

  // A frame bound to one free text block -- what the "Add text" button makes.
  // Without this the frame renders nothing, because a `txb_` id matches none
  // of the content lists searched below.
  const block = (doc.blocks ?? []).find((candidate) => candidate.nid === root);
  if (block) return <FreeBlock block={block} {...props} />;

  // A section frame renders its heading and whatever entries no other frame has
  // claimed -- including none at all. Going through `body` here would return
  // null once every entry had been pulled out, and the heading would vanish
  // with them.
  const section = orderedSections(doc).find((meta) => meta.key === root);
  if (section) {
    if (section.key === 'summary') return body(section);
    if (section.key === 'skills') return body(section);
    const entries = {
      experience: doc.experience,
      education: doc.education,
      projects: doc.projects,
    }[section.key as 'experience' | 'education' | 'projects'];
    if (!entries) return body(section);

    const Block = {
      experience: ExperienceBlock,
      education: EducationBlock,
      projects: ProjectBlock,
    }[section.key as 'experience' | 'education' | 'projects'];

    return (
      <Section title={section.label || section.key}>
        {ctx.mine(entries as { nid: string }[]).map((entry) => (
          <Block key={entry.nid} entry={entry as never} {...props} />
        ))}
      </Section>
    );
  }

  if (root === 'custom') {
    return doc.custom.map((entry) => (
      <CustomBlock key={entry.nid} section={entry} {...props} />
    ));
  }

  // A single entry, pulled out of its section onto the canvas.
  for (const entry of doc.experience) {
    if (entry.nid === root) return <ExperienceBlock entry={entry} {...props} />;
  }
  for (const entry of doc.education) {
    // `{...props}` is not optional here, and its absence was a real defect:
    // every sibling below spreads it and this one did not, so an education
    // entry pulled out onto the canvas got no `editable`, no `changed`, no
    // `locked` and none of the edit callbacks. It could not be typed into, it
    // did not light up when the assistant rewrote it, and it never reported
    // focus -- which is what tells the server to refuse the agent on a node
    // the user is holding.
    if (entry.nid === root) return <EducationBlock entry={entry} {...props} />;
  }
  for (const entry of doc.projects) {
    if (entry.nid === root) return <ProjectBlock entry={entry} {...props} />;
  }
  for (const entry of doc.custom) {
    if (entry.nid === root) return <CustomBlock section={entry} {...props} />;
  }
  return null;
}

export default DocumentFlow;
