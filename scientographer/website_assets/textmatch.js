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

// A filter: comma-separated queries that must ALL match, each a whole phrase.
// Returns a function text -> boolean, or null when the query is empty.
export function makeFilter(query, mode = "word") {
  const parts = (query || "").split(",").map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return null;
  const tests = parts.map((p) => {
    const { re } = buildMatcher([p], mode);
    return new RegExp(re.source, "i"); // non-global: test() keeps no state
  });
  return (text) => tests.every((re) => re.test(text || ""));
}
