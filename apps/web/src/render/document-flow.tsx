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

import { Fragment, useRef, type ReactNode } from 'react';
import type {
  CustomSectionNode,
  EducationNode,
  ExperienceNode,
  ListDisplay,
  ProjectNode,
  SectionMeta,
  SkillGroup,
  SkillItem,
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
   * Enter at the end of a line: open the next one.
   *
   * The gesture every list editor has, so it needs no chrome and nothing to
   * discover. Without it a résumé could be edited word for word and never
   * gain a line, and somebody wanting one more bullet had to ask the
   * assistant for it.
   */
  onSplitLine?: (nid: string) => void;
  /** Backspace in a line that is already empty: close it. */
  onRemoveLine?: (nid: string) => void;
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
  /**
   * Draw those hints at rest, rather than only when the pointer or the caret
   * is in the part of the document they belong to.
   *
   * Distinct from `placeholders`, which says a field *has* a hint at all. A
   * document built from a template is a form: every empty field in it is a
   * prompt, and that is the whole of what it has to show. A document that came
   * from somebody's own résumé is not -- a field empty there was empty in the
   * source, so a word the app supplies reads as a word the résumé contains.
   */
  prompting?: boolean;
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
  isLocked?: boolean,
  unverified?: ReadonlySet<string>
): string {
  const parts = ['node'];
  if (changed?.has(nid)) parts.push('node--changed');
  if (isLocked) parts.push('node--locked');
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
 * Whether the caret sits at the very end of the text.
 *
 * Enter only opens a new line from the end. In the middle of a sentence it
 * would have to split the words too, and a résumé bullet cut in half by a
 * stray keypress is a worse outcome than a keypress that does nothing.
 */
function atEnd(element: HTMLElement): boolean {
  const selection = window.getSelection();
  if (!selection || !selection.isCollapsed || selection.rangeCount === 0) return false;
  const range = selection.getRangeAt(0).cloneRange();
  range.selectNodeContents(element);
  range.setStart(selection.getRangeAt(0).endContainer, selection.getRangeAt(0).endOffset);
  return range.toString().length === 0;
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
  onSplitLine,
  onRemoveLine,
}: {
  as?: 'span' | 'div' | 'p' | 'li' | 'h1' | 'h2';
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
  onSplitLine?: (nid: string) => void;
  onRemoveLine?: (nid: string) => void;
}) {
  // A tool call is writing here right now. Its text is shown in place of the
  // stored value so the words appear as they are typed -- and it is *only*
  // shown: `value` below is still what the document says, so a blur commits
  // the real text and a draft that never lands leaves nothing behind.
  const target = field ? `${nid}.${field}` : nid;
  const drafted = drafts?.get(target);
  // Closed while the pen is in it, at whatever granularity the pen names.
  // A field is addressed as `nid.field` and a text node by its id, which is
  // exactly how a draft is keyed -- so the two agree by construction, and
  // locking one field of an entry does not freeze the rest of it.
  const isLocked = (locked?.has(target) || locked?.has(nid)) ?? false;
  const live = editable && !isLocked;
  const shown = drafted ?? value;
  // An empty field shows what belongs in it whenever the document is being
  // worked on, not only while this particular run happens to be live. The hint
  // is drawn by CSS from the attribute and is never text in the document.
  const hinted = live || placeholders;

  // What the element held when the caret arrived, and whether Escape has just
  // put it back. Refs rather than state: neither should redraw anything, and a
  // re-render between keydown and blur would lose them.
  const entered = useRef(value);
  const reverted = useRef(false);

  /**
   * Send what is in the element, if it is genuinely new.
   *
   * Two guards, and both are load-bearing. Against `value`, so text that
   * arrived from elsewhere while the caret sat here is not written back as if
   * the user had typed it -- a rejected agent edit reappeared that way on
   * blur. Against `entered`, so a redraw that restores what was already here
   * does not commit either, which is what makes Escape leave nothing behind.
   */
  const commit = (element: HTMLElement) => {
    const next = element.textContent ?? '';
    if (next === value || next === entered.current) return;
    entered.current = next;
    if (field) onEditField?.(`${nid}.${field}`, next);
    else onEditText?.(nid, next);
  };

  return (
    <Tag
      // Only a text node carries `data-nid`: a field is part of its entry, and
      // minting a second element with the same id would break every lookup
      // that assumes one node, one element.
      {...(field ? { 'data-field': `${nid}.${field}` } : { 'data-nid': nid })}
      className={[
        classesFor(nid, changed, isLocked, unverified),
        className,
        hinted ? 'editable' : null,
        drafted !== undefined ? 'node--drafting' : null,
      ]
        .filter(Boolean)
        .join(' ')}
      data-placeholder={hinted ? placeholder : undefined}
      contentEditable={live ? 'plaintext-only' : undefined}
      suppressContentEditableWarning
      onFocus={(event) => {
        onFocusNode?.(nid);
        // What was here when the caret arrived, so a blur can tell an edit
        // from a redraw. `value` is the current prop, and a change landing
        // from elsewhere while this is focused moves it -- comparing against
        // that made the blur commit the incoming text straight back as if the
        // user had typed it, and a rejected agent edit reappeared on release.
        entered.current = event.currentTarget.textContent ?? '';
      }}
      onKeyDown={(event) => {
        if (!live) return;

        // Escape abandons. Every dialog in this app takes it that way, and the
        // one place where the stakes are a person's own words took it as
        // "commit" -- there was no way out of a half-typed line except undo.
        if (event.key === 'Escape') {
          event.preventDefault();
          event.currentTarget.textContent = entered.current;
          reverted.current = true;
          event.currentTarget.blur();
          return;
        }

        if (event.key === 'Enter' && !event.shiftKey) {
          // Never a line break. `plaintext-only` accepts one, and a résumé
          // field is a data field: a newline inside a job title reached the
          // stored value and the PDF, where it read as a rendering fault.
          event.preventDefault();
          if (onSplitLine && atEnd(event.currentTarget)) {
            commit(event.currentTarget);
            onSplitLine(nid);
          }
          return;
        }

        // Backspace at the head of an already-empty line closes it. The
        // counterpart to Enter, and the only way to be rid of a bullet: a
        // person who cleared one was left with a bullet point marking nothing.
        if (
          event.key === 'Backspace' &&
          onRemoveLine &&
          (event.currentTarget.textContent ?? '') === ''
        ) {
          event.preventDefault();
          onRemoveLine(nid);
        }
      }}
      onBlur={(event) => {
        onFocusNode?.(null);
        if (reverted.current) {
          reverted.current = false;
          return;
        }
        commit(event.currentTarget);
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
  unverified?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
  editable?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
  onSplitLine?: (nid: string) => void;
  onRemoveLine?: (nid: string) => void;
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
  unverified?: ReadonlySet<string>;
  drafts?: ReadonlyMap<string, string>;
  editable?: boolean;
  onEditText?: (nid: string, value: string) => void;
  onFocusNode?: (nid: string | null) => void;
  onSplitLine?: (nid: string) => void;
  onRemoveLine?: (nid: string) => void;
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

/**
 * `sectionKey` is the agent's own name for this section -- the same string
 * `read_document` takes as an argument. It is here so an overlay can find the
 * region a read names; it is inert in the PDF, exactly like `data-no-break`
 * and `data-page-block` beside it, and nothing in the document reads it.
 */
function Section({
  title,
  sectionKey,
  titleNid,
  titleField,
  children,
  ...rest
}: {
  title: string;
  sectionKey?: string;
  /**
   * What the heading commits against, when it can be changed.
   *
   * A built-in section is not a node -- it is an entry in `doc.sections`
   * saying what the résumé calls this part of itself -- so it is addressed as
   * `section.<key>`, the way `personal.email` is. A custom section *is* a
   * node, and its heading is the `label` field on it.
   *
   * Absent for the flowing export and the print route, where nothing is
   * editable and a plain `<h2>` is what belongs on the page.
   */
  titleNid?: string;
  titleField?: string;
  children: React.ReactNode;
} & Partial<Omit<DocumentFlowProps, 'doc'>>) {
  return (
    <section className="section" data-no-break data-section={sectionKey}>
      {/* Editable only where editing happens. The print route and the ATS
          export render this same component with `editable` unset, and a PDF
          must carry no editing affordance -- so those get the plain heading
          they always had, attributes and all. */}
      {titleNid && titleField && rest.editable ? (
        <Editable
          as="h2"
          className="section__title"
          nid={titleNid}
          field={titleField}
          value={title}
          placeholder="Heading"
          changed={rest.changed}
          locked={rest.locked}
          drafts={rest.drafts}
          editable={rest.editable}
          placeholders={rest.placeholders}
          onEditField={rest.onEditField}
          onFocusNode={rest.onFocusNode}
        />
      ) : (
        <h2
          className="section__title"
          data-page-block={`title:${title}`}
          data-page-heading
        >
          {title}
        </h2>
      )}
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
          {/* A comma joins two values. It is drawn as a hint, not as content,
              the moment either side is only a hint.

              As real text beside an empty location it read as an employer's
              address: an imported résumé with no location showed
              "L'Oréal Paris, Location" -- a word nobody typed, in a place a
              word belongs, punctuated as though it were the résumé's own. The
              hint on its own is muted and italic and reads as a prompt; the
              comma was what made it read as a fact. */}
          {(entry.company || rest.placeholders) &&
            (entry.location || rest.placeholders) &&
            (entry.company && entry.location ? (
              ', '
            ) : (
              <span className="hint">, </span>
            ))}
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
          unverified={rest.unverified}
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
 * Whether a run of short items is stacked rather than comma-joined.
 *
 * A skills group is a run of short tokens and reads best as one dense line —
 * that is also the shape an ATS parses most reliably. Certifications and awards
 * are not that: the entries are long and carry commas of their own, so joining
 * them with ", " produces a run of text where the boundary between two
 * credentials is indistinguishable from the comma inside one of them.
 *
 * That rule is a good default and was the *only* rule, which made the shape of
 * a section a property of its words rather than a choice. "List my technical
 * skills as bullets" could not be honoured at all — the setting did not exist,
 * so the assistant reported the product could not do it, and the only way to
 * get a list was to write an item long enough to trip the length test.
 *
 * A document that says which it wants is now obeyed. The heuristic is what
 * `auto` means, and stays here alone: the server does not second-guess it,
 * because two copies of one rule is two rules.
 */
function stacked(items: SkillItem[], display?: ListDisplay): boolean {
  if (display === 'list') return true;
  if (display === 'inline') return false;
  return items.some((item) => item.text.includes(',') || item.text.length > 48);
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
      unverified={rest.unverified}
      drafts={rest.drafts}
      editable={rest.editable}
      onEditText={rest.onEditText}
      onFocusNode={rest.onFocusNode}
      // The comma between skills is a separator the renderer draws, not text a
      // caret can reach -- so removing a skill is how its comma goes. Backspace
      // on an emptied skill removes the item; Enter adds one. The same gesture
      // a bullet has, wired to the same handlers.
      onSplitLine={rest.onSplitLine}
      onRemoveLine={rest.onRemoveLine}
    />
  );

  // The colon after the label, on the same rule as the comma on the employer
  // line: punctuation is content, and content that joins two things is only
  // drawn when there are two things. A real label gets a real colon; an empty
  // one gets the colon as part of its hint, and only where hints are drawn at
  // all -- never in an export, which would otherwise read ": Python".
  const colon = (group: SkillGroup) =>
    group.label ? (
      ':'
    ) : rest.editable || rest.placeholders ? (
      <span className="hint">:</span>
    ) : null;

  return (
    <>
      {groups.map((group) => {
        const field = fieldsOf(group.nid, rest);
        return stacked(group.items, group.display) ? (
          <div
            key={group.nid}
            className="skills__row"
            data-nid={group.nid}
            data-page-block={group.nid}
          >
            <Editable className="skills__label" {...field('label', group.label, 'Group')} />
            {colon(group)}
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
            <Editable className="skills__label" {...field('label', group.label, 'Group')} />
            {colon(group)}{' '}
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
    <Section
      title={section.label || section.key}
      sectionKey={section.key}
      titleNid={section.nid}
      titleField="label"
      {...rest}
    >
      {section.text && (
        <Editable
          as="p"
          className="entry__detail"
          nid={section.text.nid}
          value={section.text.text}
          changed={rest.changed}
          locked={rest.locked}
          unverified={rest.unverified}
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
      {section.strings.length > 0 && <StringList section={section} {...rest} />}
    </Section>
  );
}

/**
 * The `strings` of a custom section: certifications, awards, languages.
 *
 * The same widget as a skills group and drawn by the same rules, which it was
 * not — this was comma-joined with no alternative, so a Certifications section
 * got exactly the run of text `stacked` exists to prevent, and no setting could
 * change it. One credential ending and the next beginning was indistinguishable
 * from the comma inside one of them.
 */
function StringList({
  section,
  ...rest
}: { section: CustomSectionNode } & Omit<DocumentFlowProps, 'doc'>) {
  const entry = (line: SkillItem, as?: 'li') => (
    <Editable
      as={as}
      nid={line.nid}
      value={line.text}
      changed={rest.changed}
      locked={rest.locked}
      unverified={rest.unverified}
      drafts={rest.drafts}
      editable={rest.editable}
      onEditText={rest.onEditText}
      onFocusNode={rest.onFocusNode}
    />
  );

  return stacked(section.strings, section.display) ? (
    <ul className="skills__list">
      {section.strings.map((line) => (
        <Fragment key={line.nid}>{entry(line, 'li')}</Fragment>
      ))}
    </ul>
  ) : (
    <div className="skills__row">
      {section.strings.map((line, index) => (
        <Fragment key={line.nid}>
          {index > 0 ? ', ' : ''}
          {entry(line)}
        </Fragment>
      ))}
    </div>
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
          <Section
            key={meta.key}
            sectionKey={meta.key}
            title={meta.label || 'Summary'}
            titleNid="section"
            titleField={meta.key}
            {...props}
          >
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
          <Section
            key={meta.key}
            sectionKey={meta.key}
            title={meta.label || 'Experience'}
            titleNid="section"
            titleField={meta.key}
            {...props}
          >
            {mine(doc.experience).map((entry) => (
              <ExperienceBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'education':
        return mine(doc.education).length ? (
          <Section
            key={meta.key}
            sectionKey={meta.key}
            title={meta.label || 'Education'}
            titleNid="section"
            titleField={meta.key}
            {...props}
          >
            {mine(doc.education).map((entry) => (
              <EducationBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'projects':
        return mine(doc.projects).length ? (
          <Section
            key={meta.key}
            sectionKey={meta.key}
            title={meta.label || 'Projects'}
            titleNid="section"
            titleField={meta.key}
            {...props}
          >
            {mine(doc.projects).map((entry) => (
              <ProjectBlock key={entry.nid} entry={entry} {...props} />
            ))}
          </Section>
        ) : null;
      case 'skills':
        return doc.skills.length ? (
          <Section
            key={meta.key}
            sectionKey={meta.key}
            title={meta.label || 'Skills'}
            titleNid="section"
            titleField={meta.key}
            {...props}
          >
            <SkillsBlock groups={doc.skills} {...props} />
          </Section>
        ) : null;
      default: {
        // A section we have no schema for, keyed by the résumé's own heading.
        // It is resolved here rather than appended after the loop so that
        // Publications sitting between Experience and Education stays there:
        // the order came from the source document, and rendering custom
        // sections last would reshuffle somebody's résumé on the way in.
        const own = doc.custom.find((section) => section.key === meta.key);
        return own ? <CustomBlock key={meta.key} section={own} {...props} /> : null;
      }
    }
  };

  // Which custom sections the section order already places, so the tail below
  // adds only the ones it does not.
  const placed = new Set(
    orderedSections(doc)
      .map((meta) => meta.key)
      .filter((key) => doc.custom.some((section) => section.key === key))
  );

  // Kept as field names, not values: each part of the contact line is its own
  // editable run, so a wrong digit in a phone number is a click and a keypress
  // rather than retyping the whole line.
  const contact = (
    ['location', 'phone', 'email', 'linkedin', 'github', 'website'] as const
  ).filter((key) => doc.personal[key]);

  const personal = fieldsOf('personal', props);

  /**
   * The headshot, on the templates that have one.
   *
   * Where it sits is the template's business -- the header is a single frame,
   * so `.flow--portrait` and friends place it with ordinary CSS. What is
   * decided here is only whether it is drawn at all.
   *
   * The empty state is a placeholder, and it is drawn on the same terms as the
   * text hints beside it: only while the document is being edited or previewed
   * on a card, never in the export. An empty grey square is a template showing
   * you where a photo goes; in a PDF sent to an employer it is a hole.
   */
  const photoSlot =
    doc.personal.photo || props.editable || props.placeholders ? (
      // `data-field` so the pen can find it: the overlay resolves whatever
      // the protocol named, and a `set_field` on `personal.photo` reports
      // exactly that string. Same addressing every other personal field uses.
      <div className="flow__photo" data-page-block="photo" data-field="personal.photo">
        {doc.personal.photo ? (
          <img
            src={`/api/v1/assets/${doc.personal.photo}`}
            alt=""
            /* An id that names no asset -- one the model invented rather than
               read off UPLOADS -- would otherwise draw the browser's broken
               image glyph, and draw it into the PDF. Hiding the element leaves
               the header as it would be with no photo at all. */
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : (
          <span className="flow__photo-hint" aria-hidden="true">
            Photo
          </span>
        )}
      </div>
    ) : null;

  const header = (
    <header className="flow__header" data-page-block="header">
      {photoSlot}
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
  // `flow--prompting` says the empty fields in this document are a form to
  // fill in, not gaps in somebody's résumé. It rides on the root for the same
  // reason the template does: a frame on a canvas page and the print route
  // both resolve it without being told.
  const flow = [
    'flow',
    `flow--${doc.template ?? 'plain'}`,
    props.prompting ? 'flow--prompting' : null,
  ]
    .filter(Boolean)
    .join(' ');

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
      {/* Anything the order did not account for. A custom section made after
          the document was imported has no `sectionMeta` row of its own, and
          without this it would render nowhere at all. */}
      {doc.custom
        .filter((section) => !placed.has(section.key))
        .map((section) => (
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
  unverified,
  drafts,
  editable,
  onEditText,
  onFocusNode,
}: {
  node: TextNode;
  changed?: ReadonlySet<string>;
  locked?: ReadonlySet<string>;
  unverified?: ReadonlySet<string>;
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
      unverified={unverified}
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
      <Section
        title={section.label || section.key}
        sectionKey={section.key}
        titleNid="section"
        titleField={section.key}
        {...props}
      >
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
