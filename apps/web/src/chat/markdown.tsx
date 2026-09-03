/**
 * The small slice of Markdown the assistant actually writes.
 *
 * Not a Markdown implementation, and not trying to be. The model emits bold,
 * italics, inline code, short headings and the two kinds of list; everything
 * else it has never once produced in this app. Covering that slice is a few
 * dozen lines, where `react-markdown` is fifteen packages of unified/remark in
 * a project whose entire runtime dependency list is four entries — and which
 * already hand-rolls its NDJSON reader and its document renderer for the same
 * reason.
 *
 * **Everything here produces React elements.** There is no
 * `dangerouslySetInnerHTML` anywhere and there must never be one, because this
 * text is not merely model output: the assistant quotes the user's resume, and
 * that resume arrived in an uploaded PDF that we do not control. Building
 * elements rather than HTML makes injection impossible by construction rather
 * than by escaping correctly every time.
 *
 * Two deliberate omissions:
 *
 * *No `_underscore_` emphasis.* A resume is full of `build_script` and
 * `my_project`, and `_[^_]+_` matches straight across the gap between two of
 * them. Models use asterisks; underscores cost more than they are worth.
 *
 * *Links render as text, not anchors.* A URL in this pane can originate in a
 * hostile PDF, and a clickable link is a phishing surface bought for no real
 * benefit — the user can still read and copy it.
 */

import type { ReactNode } from 'react';

/** Inline code, then bold, then italic. Order matters: `**` before `*`. */
const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;

const HEADING = /^(#{1,6})\s+(.*)$/;
const UNORDERED = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const FENCE = /^\s*```/;

/**
 * Split one line into inline spans.
 *
 * An unterminated marker — `**Why` mid-stream, before its closing pair has
 * arrived — simply does not match, so it renders as the literal characters the
 * model has sent so far and resolves itself on the next delta.
 */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let index = 0;

  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));

    const token = match[0];
    const key = `${keyPrefix}-${index++}`;
    if (token.startsWith('`')) {
      out.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**')) {
      out.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else {
      out.push(<em key={key}>{token.slice(1, -1)}</em>);
    }
    last = at + token.length;
  }

  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Lines joined into one paragraph, with the model's own breaks kept. */
function paragraph(lines: string[], key: string): ReactNode {
  return (
    <p key={key}>
      {lines.map((line, index) => (
        <span key={index}>
          {index > 0 && <br />}
          {inline(line, `${key}-${index}`)}
        </span>
      ))}
    </p>
  );
}

export function renderMarkdown(text: string): ReactNode[] {
  const lines = text.split('\n');
  const blocks: ReactNode[] = [];
  let buffer: string[] = [];
  let index = 0;

  const flush = () => {
    if (!buffer.length) return;
    blocks.push(paragraph(buffer, `p${index++}`));
    buffer = [];
  };

  for (let cursor = 0; cursor < lines.length; cursor += 1) {
    const line = lines[cursor];

    if (FENCE.test(line)) {
      flush();
      const body: string[] = [];
      cursor += 1;
      while (cursor < lines.length && !FENCE.test(lines[cursor])) {
        body.push(lines[cursor]);
        cursor += 1;
      }
      blocks.push(<pre key={`c${index++}`}>{body.join('\n')}</pre>);
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      // Rendered as one weight regardless of depth: this is a chat message,
      // not a document, and a model's "###" carries no reliable hierarchy.
      blocks.push(
        <p className="revision__heading" key={`h${index++}`}>
          {inline(heading[2], `h${index}`)}
        </p>
      );
      continue;
    }

    if (UNORDERED.test(line) || ORDERED.test(line)) {
      flush();
      const ordered = ORDERED.test(line);
      const items: string[] = [];
      let start = 1;

      while (cursor < lines.length) {
        const item = ordered ? ORDERED.exec(lines[cursor]) : UNORDERED.exec(lines[cursor]);
        if (item) {
          if (ordered && !items.length) start = Number(item[1]) || 1;
          items.push(ordered ? item[2] : item[1]);
          cursor += 1;
          continue;
        }
        // A blank line between two items is a break in the source, not the end
        // of the list — models space numbered points out routinely. Stop only
        // at real content.
        if (!lines[cursor].trim() && cursor + 1 < lines.length) {
          const next = ordered
            ? ORDERED.test(lines[cursor + 1])
            : UNORDERED.test(lines[cursor + 1]);
          if (next) {
            cursor += 1;
            continue;
          }
        }
        break;
      }
      cursor -= 1;

      const key = `l${index++}`;
      const children = items.map((item, position) => (
        <li key={position}>{inline(item, `${key}-${position}`)}</li>
      ));
      blocks.push(
        ordered ? (
          // `start` honours the number the model wrote, so a list that resumes
          // after other prose does not silently restart at one.
          <ol className="revision__list" key={key} start={start}>
            {children}
          </ol>
        ) : (
          <ul className="revision__list" key={key}>
            {children}
          </ul>
        )
      );
      continue;
    }

    buffer.push(line);
  }

  flush();
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  return <>{renderMarkdown(text)}</>;
}
