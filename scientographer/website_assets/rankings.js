// SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
// SPDX-License-Identifier: MIT
// Bridge papers and the rankings behind "top papers", as pure functions over the
// site's citation data, so main.js only wires them to the map and they can be
// tested on a hand-made graph with node.
//
// The citation data is the CSR adjacency main.js parses from edges_out.bin /
// edges_in.bin: { n, offsets: Uint32Array(n + 1), targets: Uint32Array }, where
// paper i's neighbours are targets[offsets[i] .. offsets[i + 1]). A grouping is
// passed as `groupOf`: one integer per paper, its community (or topic) id, or a
// negative number when the paper has none.

// ── Bridge papers ────────────────────────────────────────────────────────────
// A bridge paper links many communities rather than sitting inside one. The
// measure is the participation coefficient of Guimerà & Amaral (2005),
// "Functional cartography of complex metabolic networks", Nature 433:895-900:
// P_i = 1 - sum over communities c of (k_ic / k_i)^2, where k_i is the paper's
// number of links and k_ic its links to papers of community c. P is 0 when every
// link stays in one community and approaches 1 when the links spread evenly over
// many.
//
// Bridge papers are the BRIDGE_COUNT papers with the highest P × ln(links): P
// rewards links spread over communities, ln(links) how many links there are to
// spread, so a paper with a handful of citations over a few communities does not
// outrank one that connects hundreds. A paper needs at least BRIDGE_MIN_LINKS
// links. A fixed count, not a cutoff on P such as Guimerà & Amaral's 0.62: at a
// fine resolution any well-cited paper's links reach many small communities, and
// on the motor-learning map P > 0.62 marked 3,033 of 22,982 papers at γ = 0.0015.
export const BRIDGE_MIN_LINKS = 10;
export const BRIDGE_COUNT = 50;

function neighbors(csr, i) {
  return csr.targets.subarray(csr.offsets[i], csr.offsets[i + 1]);
}

// Every paper's participation coefficient, its number of links and the number of
// communities those links reach. Citations are taken as undirected links: a
// paper's neighbours are the papers it cites and the papers citing it, each
// counted once (a mutual citation is one link) and self-citations left out. A
// neighbour without a community counts as a community of its own, so a link to
// it never looks like a link into the paper's own community.
export function participationCoefficients(outCSR, inCSR, groupOf) {
  const n = outCSR.n;
  const coefficient = new Float64Array(n);
  const links = new Uint32Array(n);
  const communitiesReached = new Uint32Array(n);
  // lastSeen[j] === i when j was already counted as a neighbour of i.
  const lastSeen = new Int32Array(n).fill(-1);
  const linksPerCommunity = new Map();
  for (let i = 0; i < n; i++) {
    linksPerCommunity.clear();
    let linkCount = 0;
    for (const csr of [outCSR, inCSR]) {
      for (const j of neighbors(csr, i)) {
        if (j === i || lastSeen[j] === i) continue;
        lastSeen[j] = i;
        linkCount++;
        const community = groupOf[j] >= 0 ? groupOf[j] : -1 - j;
        linksPerCommunity.set(community, (linksPerCommunity.get(community) || 0) + 1);
      }
    }
    links[i] = linkCount;
    communitiesReached[i] = linksPerCommunity.size;
    if (linkCount === 0) continue; // no links: P = 0
    let sumOfSquares = 0;
    for (const count of linksPerCommunity.values()) sumOfSquares += (count / linkCount) ** 2;
    coefficient[i] = 1 - sumOfSquares;
  }
  return { coefficient, links, communitiesReached };
}

// A paper's bridge score, P × ln(links); 0 below BRIDGE_MIN_LINKS links.
export function bridgeScore(coefficient, links) {
  return links >= BRIDGE_MIN_LINKS ? coefficient * Math.log(links) : 0;
}

// 1 for the bridge papers of a participationCoefficients() result, else 0: the
// `count` papers with the highest bridge score (ties: more links first), leaving
// out papers whose score is 0.
export function bridgePapers({ coefficient, links }, count = BRIDGE_COUNT) {
  const n = coefficient.length;
  const scored = [];
  for (let i = 0; i < n; i++) {
    const score = bridgeScore(coefficient[i], links[i]);
    if (score > 0) scored.push([score, links[i], i]);
  }
  scored.sort((a, b) => b[0] - a[0] || b[1] - a[1] || a[2] - b[2]);
  const bridge = new Uint8Array(n);
  for (const [, , i] of scored.slice(0, count)) bridge[i] = 1;
  return bridge;
}

// ── Rankings ─────────────────────────────────────────────────────────────────
// The four ways to rank a group's top papers (the menu labels), and `describe`,
// the value shown next to each paper (given the group's name: "community", "topic").
// For a topic, "within the community" means within the topic.
export const RANKINGS = {
  citations: { label: "Citations", describe: (value) => `${value} citations` },
  citations_within_group: {
    label: "Citations within the community",
    describe: (value, groupLabel) => `${value} citations from this ${groupLabel}`,
  },
  citations_per_year: {
    label: "Citations per year",
    describe: (value) => `${value.toFixed(1)} citations per year`,
  },
  pagerank: { label: "PageRank", describe: (value) => `PageRank ${value.toPrecision(3)}` },
};
export const DEFAULT_RANKING = "citations";

// How many citations each paper receives from papers of its own group. Papers
// without a group get 0 (they have no group to rank in).
export function citationsWithinGroup(inCSR, groupOf) {
  const counts = new Float64Array(inCSR.n);
  for (let i = 0; i < inCSR.n; i++) {
    if (groupOf[i] < 0) continue;
    let count = 0;
    for (const citing of neighbors(inCSR, i)) if (groupOf[citing] === groupOf[i]) count++;
    counts[i] = count;
  }
  return counts;
}

// One score per paper for `ranking` (a RANKINGS key); NaN leaves the paper out
// of that ranking. `papers` are the nodes.json records (indegree, year,
// pagerank); `latestYear` is the corpus's latest publication year.
export function rankingScores(ranking, papers, inCSR, groupOf, latestYear) {
  const scores = new Float64Array(papers.length);
  if (ranking === "citations_within_group") {
    scores.set(citationsWithinGroup(inCSR, groupOf));
    return scores;
  }
  for (let i = 0; i < papers.length; i++) {
    const paper = papers[i];
    if (ranking === "citations") {
      scores[i] = paper.indegree || 0;
    } else if (ranking === "citations_per_year") {
      // Years since publication, counting the publication year itself: a paper of
      // the latest year has had one year to be cited.
      scores[i] = paper.year != null ? (paper.indegree || 0) / Math.max(1, latestYear - paper.year + 1) : NaN;
    } else if (ranking === "pagerank") {
      scores[i] = paper.pagerank != null ? paper.pagerank : NaN;
    } else {
      throw new Error(`unknown ranking ${ranking}`);
    }
  }
  return scores;
}

// group id -> the indices of its `count` highest-scoring papers, best first, for
// every group in `groups` (a Set of group ids). Ties keep the papers' order in
// nodes.json, which is how the build ranks them (a stable sort), so the
// "Citations" lists match the build's `top_papers`.
export function topPapersByGroup(scores, groupOf, groups, count) {
  const top = new Map();
  for (const gid of groups) top.set(gid, []);
  for (let i = 0; i < scores.length; i++) {
    const list = top.get(groupOf[i]);
    const score = scores[i];
    if (!list || Number.isNaN(score)) continue;
    if (list.length === count && score <= scores[list[count - 1]]) continue;
    // Insert after every paper scoring at least as much (earlier papers win ties).
    let position = list.length;
    while (position > 0 && scores[list[position - 1]] < score) position--;
    list.splice(position, 0, i);
    if (list.length > count) list.pop();
  }
  return top;
}
