import { lessonSections } from "./lessonSections";

// A prepared lesson as notes: an "at a glance" table, then a short headed paragraph
// per part (what it is, why it matters, ...), with the sentence being spoken marked.

const PART_HEADING = {
  what: "What it is",
  why: "Why it matters",
  how: "How it works",
  example: "Example",
  mistake: "Common mistake",
  recap: "Remember",
};
const PART_ORDER = Object.keys(PART_HEADING);
const GLANCE_CHARS = 110;
const shorten = (text) => {
  if (text.length <= GLANCE_CHARS) return text;
  const cut = text.slice(0, GLANCE_CHARS);
  return `${cut.slice(0, cut.lastIndexOf(" ")).replace(/[,;:]$/, "")}…`;
};

export default function LessonNotes({ title, sentences, current = -1 }) {
  const sections = lessonSections(sentences, PART_ORDER);
  const glance = PART_ORDER.map((part) => sections.find((section) => section.part === part))
    .filter(Boolean)
    .map((section) => [section.part, shorten(section.sentences[0].text)]);

  return (
    <article className="mt-lesson" aria-label={`Lesson: ${title}`}>
      <h3 className="mt-lesson-title">{title}</h3>
      {glance.length > 1 && (
        <table className="mt-lesson-glance">
          <caption>At a glance</caption>
          <tbody>
            {glance.map(([part, line]) => (
              <tr key={part}>
                <th scope="row">{PART_HEADING[part]}</th>
                <td>{line}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {sections.map((section, index) => (
        <section key={index} className="mt-lesson-part">
          {section.part && <h4>{PART_HEADING[section.part]}</h4>}
          <p>
            {section.sentences.map((sentence, number) => {
              const speaking = sentence.steps.includes(current);
              const text = number ? ` ${sentence.text}` : sentence.text;
              return speaking ? (
                <mark key={number} aria-current="true">
                  {text}
                </mark>
              ) : (
                <span key={number}>{text}</span>
              );
            })}
          </p>
        </section>
      ))}
    </article>
  );
}
