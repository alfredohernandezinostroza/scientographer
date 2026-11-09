// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// Text matching shared by the map's search and filters and the Word map tab.
//
// Queries use the syntax of literature databases (PubMed, Scopus, Web of
// Science):
//   speech children            both words (a space means AND)
//   speech AND children        the same, spelled out
//   speech OR language         either word ( | is short for OR)
//   speech NOT apraxia         the first without the second
//   (speech OR language) AND children
//   "motor learning"           the exact phrase
// AND, OR and NOT are operators only in capitals; quote them to search the
// word itself. NOT binds tightest, then AND, then OR. Commas outside quotes
// separate words like spaces; inside quotes they are matched literally, so
// whole titles and "Surname, Name" can be pasted.
//
// How each word is matched depends on the mode:
//   word      whole words, plus regular plurals: "rat" finds rat/rats, not rate,
//             rationale; "dance" finds dance/dances, not guidance or impedance
//   prefix    words that start with the term: "bird" finds birdsong
//   substring anywhere, also inside words (the old behaviour)
//   regex     the text between the operators is a JavaScript regular
//             expression (case-insensitive); there | and ( ) belong to the
//             expression, so only AND, OR and NOT (with spaces around them)
//             combine expressions
// Matching ignores case. Irregular plurals aren't handled: search mouse OR mice.

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
  return { re: new RegExp(start + body + end, "gi"), forms: [`"${phrase.trim()}"`] };
}

// ── Parsing ────────────────────────────────────────────────────────────────
// Tokens: { t: "term" | "phrase" | "and" | "or" | "not" | "(" | ")", v }.
const OPERATORS = { AND: "and", OR: "or", NOT: "not" };

function tokenize(query) {
  const out = [];
  let i = 0;
  const s = query;
  while (i < s.length) {
    const c = s[i];
    if (/[\s,]/.test(c)) { i++; continue; }
    if (c === '"') {
      const end = s.indexOf('"', i + 1);
      const phrase = s.slice(i + 1, end < 0 ? s.length : end); // an unclosed quote runs to the end
      if (phrase.trim()) out.push({ t: "phrase", v: phrase });
      i = end < 0 ? s.length : end + 1;
      continue;
    }
    if (c === "(" || c === ")") { out.push({ t: c }); i++; continue; }
    if (c === "|") { out.push({ t: "or" }); i++; continue; }
    let j = i;
    while (j < s.length && !/[\s,"()|]/.test(s[j])) j++;
    const word = s.slice(i, j);
    out.push(OPERATORS[word] ? { t: OPERATORS[word] } : { t: "term", v: word });
    i = j;
  }
  return out;
}

// In the regular-expression mode only AND, OR and NOT with spaces around them
// are operators; everything between them is one expression, spaces included.
// A part that is entirely one quoted phrase is still taken literally.
function tokenizeRegex(query) {
  const out = [];
  for (const part of query.split(/(?:^|\s+)(AND|OR|NOT)(?=\s+|$)/)) {
    if (OPERATORS[part]) { out.push({ t: OPERATORS[part] }); continue; }
    const text = part.trim();
    if (!text) continue;
    const quoted = /^"([^"]*)"$/.exec(text);
    out.push(quoted ? { t: "phrase", v: quoted[1] } : { t: "term", v: text });
  }
  return out;
}

// Recursive descent: or := and (OR and)* ; and := not ([AND] not)* ;
// not := NOT not | atom ; atom := ( or ) | term | phrase.
// Nodes: { op: "or" | "and", args } | { op: "not", arg } | { op: "leaf", re, forms }.
function parse(tokens, leaf) {
  let pos = 0;
  const peek = () => tokens[pos];
  const fail = (msg) => { throw new SyntaxError(msg); };
  const startsOperand = (tok) => tok && (tok.t === "term" || tok.t === "phrase" || tok.t === "(" || tok.t === "not");

  function or() {
    const args = [and()];
    while (peek()?.t === "or") {
      pos++;
      if (!startsOperand(peek())) fail("OR needs a word after it");
      args.push(and());
    }
    return args.length === 1 ? args[0] : { op: "or", args };
  }
  function and() {
    const args = [not()];
    for (;;) {
      if (peek()?.t === "and") {
        pos++;
        if (!startsOperand(peek())) fail("AND needs a word after it");
      } else if (!startsOperand(peek())) break;
      args.push(not());
    }
    return args.length === 1 ? args[0] : { op: "and", args };
  }
  function not() {
    if (peek()?.t === "not") {
      pos++;
      if (!startsOperand(peek())) fail("NOT needs a word after it");
      return { op: "not", arg: not() };
    }
    return atom();
  }
  function atom() {
    const tok = tokens[pos++];
    if (!tok) fail("the query ends too early");
    if (tok.t === "(") {
      if (peek()?.t === ")") fail("empty brackets");
      const inner = or();
      if (tokens[pos++]?.t !== ")") fail("a bracket is not closed");
      return inner;
    }
    if (tok.t === "term" || tok.t === "phrase") return { op: "leaf", ...leaf(tok) };
    if (tok.t === ")") fail("a closing bracket has no opening one");
    fail(`${tok.t.toUpperCase()} needs a word before it`);
  }

  const tree = or();
  if (pos < tokens.length) fail(tokens[pos].t === ")" ? "a closing bracket has no opening one" : "unexpected text");
  return tree;
}

// The query as a tree, or null when it is empty. Throws a SyntaxError for a
// malformed query or a regular expression that does not compile.
export function parseQuery(query, mode = "word") {
  const tokens = mode === "regex" ? tokenizeRegex(query || "") : tokenize(query || "");
  if (!tokens.length) return null;
  const leaf = (tok) => (tok.t === "phrase" ? literalPhrase(tok.v) : buildMatcher([tok.v], mode));
  return parse(tokens, leaf);
}

function evaluate(node, text) {
  switch (node.op) {
    case "leaf": node.re.lastIndex = 0; return node.re.test(text);
    case "not": return !evaluate(node.arg, text);
    case "and": return node.args.every((a) => evaluate(a, text));
    default: return node.args.some((a) => evaluate(a, text));
  }
}

// The leaves a matching text must or may contain (not those under a NOT).
function positiveLeaves(node, negated = false, out = []) {
  if (node.op === "leaf") { if (!negated) out.push(node); }
  else if (node.op === "not") positiveLeaves(node.arg, !negated, out);
  else for (const a of node.args) positiveLeaves(a, negated, out);
  return out;
}

// { test(text) -> boolean, count(text) -> mentions of the searched words in a
// matching text (0 otherwise), forms (what is searched, for display) }, or null
// for an empty query.
export function makeQuery(query, mode = "word") {
  const tree = parseQuery(query, mode);
  if (!tree) return null;
  const leaves = positiveLeaves(tree);
  const test = (text) => evaluate(tree, text || "");
  const count = (text) => {
    if (!test(text)) return 0;
    let n = 0;
    for (const l of leaves) n += (text.match(l.re) || []).length;
    return Math.max(1, n); // a paper matching only through NOT still counts once
  };
  return { test, count, forms: [...new Set(leaves.flatMap((l) => l.forms))] };
}

// A filter: a function text -> boolean, or null when the query is empty.
export function makeFilter(query, mode = "word") {
  return makeQuery(query, mode)?.test ?? null;
}
