// How a prepared lesson's spoken pieces are grouped for reading.

const SENTENCES_PER_PARAGRAPH = 3;

const ends = (text) => /[.!?]["')\]]?$/.test(text.trim());

// A long sentence is spoken in pieces cut at a comma; join them back for reading.
// Returns [{ text, steps: [piece indexes], part }].
export const wholeSentences = (pieces) => {
  const sentences = [];
  pieces.forEach((piece, step) => {
    const last = sentences[sentences.length - 1];
    if (last && !ends(last.text)) {
      last.text = `${last.text} ${piece.text}`;
      last.steps.push(step);
    } else {
      sentences.push({ text: piece.text, steps: [step], part: piece.part });
    }
  });
  return sentences;
};

// Neighbouring sentences of the same part make one section. Without parts, the
// sentences are cut into short paragraphs.
export const lessonSections = (pieces, knownParts) => {
  const sentences = wholeSentences(pieces);
  const sections = [];
  sentences.forEach((sentence, index) => {
    const part = knownParts.includes(sentence.part) ? sentence.part : null;
    const last = sections[sections.length - 1];
    const same = part
      ? last?.part === part
      : last && !last.part && index % SENTENCES_PER_PARAGRAPH !== 0;
    if (same) last.sentences.push(sentence);
    else sections.push({ part, sentences: [sentence] });
  });
  return sections;
};
