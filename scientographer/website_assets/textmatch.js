// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// Text matching shared by the map's search and filters and the Word map tab.
//
// Modes:
//   word      whole words, plus regular plurals: "rat" finds rat/rats, not rate,
//             rationale; "dance" finds dance/dances, not guidance or impedance
//   prefix    words that start with the term: "bird" finds birdsong
//   substring anywhere, also inside words (the old behaviour)
//   regex     each term is a JavaScript regular expression (case-insensitive)
// Matching ignores case. Irregular plurals aren't handled: search "mouse mice".

export const MATCH_MODES = [
  { value: "word", label: "Whole words (dance ≠ guidance)" },
  { value: "prefix", label: "Word beginnings (bird → birdsong)" },
  { value: "substring", label: "Anywhere (rat → rationale)" },
  { value: "regex", label: "Regular expression" },
];

export function plurals(word) {
  const forms = new Set([word, word + "s"]);
  if (/(?:s|x|z|ch|sh)$/i.test(word)) forms.add(word + "es");
  if (/[^aeiou]y$/i.test(word)) forms.add(word.slice(0, -1) + "ies");
  return [...forms];
}

export const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Quoted phrases stay whole; bare words are separate terms.
export function parseTerms(str) {
  const out = [];
  const re = /"([^"]+)"|(\S+)/g;
  let m;
  while ((m = re.exec(str || ""))) {
    const w = (m[1] || m[2]).trim();
    if (w) out.push(w);
  }
  return out;
}

// One regular expression matching any of `terms` (OR), as { re (global,
// case-insensitive), forms (what is searched, for display) }, or null for no
// terms. A regex term that does not compile throws a SyntaxError.
export function buildMatcher(terms, mode = "word") {
  if (!terms.length) return null;
  if (mode === "regex") {
    return { re: new RegExp(terms.map((t) => `(?:${t})`).join("|"), "gi"), forms: terms };
  }
  if (mode === "substring") {
    return { re: new RegExp(terms.map(escapeRegExp).join("|"), "gi"), forms: terms };
  }
  if (mode === "prefix") {
    return {
      re: new RegExp("\\b(?:" + terms.map(escapeRegExp).join("|") + ")", "gi"),
      forms: terms.map((t) => t + "…"),
    };
  }
  const forms = terms.flatMap(plurals).sort();
  return { re: new RegExp("\\b(?:" + forms.map(escapeRegExp).join("|") + ")\\b", "gi"), forms };
}

// A quoted phrase is matched literally, in any matching mode: case and runs of
// spaces aside, the text must contain exactly those characters (commas, brackets
// and all), starting and ending at word boundaries.
function literalPhrase(phrase) {
  const body = escapeRegExp(phrase.trim()).replace(/\s+/g, "\\s+");
  const start = /^\w/.test(phrase.trim()) ? "\\b" : "";
  const end = /\w$/.test(phrase.trim()) ? "\\b" : "";
  return new RegExp(start + body + end, "i");
}

// A filter: comma-separated parts that must ALL match; within a part,
// alternatives separated by | of which ANY must match. A quoted phrase is taken
// literally, commas and | included, so whole titles can be pasted:
//   "Title one" | "Title two"       either paper
//   dance | music, balance          (dance or music) and balance
// In the regular-expression mode | belongs to the expression, so
// (motor|sensory) adaptation keeps its meaning; only a list made entirely of
// quoted phrases ("a" | "b") is split there.
// Returns a function text -> boolean, or null when the query is empty.
const QUOTED = /^\u0000(\d+)\u0000$/;

function alternatives(part, mode) {
  const alts = part.split("|").map((alt) => alt.trim()).filter(Boolean);
  if (mode === "regex" && !alts.every((alt) => QUOTED.test(alt))) return [part.trim()].filter(Boolean);
  return alts;
}

export function makeFilter(query, mode = "word") {
  // Set quoted phrases aside so their commas and | are not read as operators.
  const phrases = [];
  const masked = (query || "").replace(/"([^"]*)"/g, (_, phrase) => `\u0000${phrases.push(phrase) - 1}\u0000`);
  const unmask = (t) => t.replace(/\u0000(\d+)\u0000/g, (_, i) => phrases[+i]);
  const groups = masked
    .split(",")
    .map((part) => alternatives(part, mode))
    .filter((alts) => alts.length);
  if (!groups.length) return null;
  const tests = groups.map((alts) =>
    alts.map((alt) => {
      const quoted = QUOTED.exec(alt);
      if (quoted) return literalPhrase(phrases[+quoted[1]]);
      const { re } = buildMatcher([unmask(alt)], mode);
      return new RegExp(re.source, "i"); // non-global: test() keeps no state
    }));
  return (text) => tests.every((any) => any.some((re) => re.test(text || "")));
}
