import igraph as ig
import pandas as pd
import pytest

from scientographer.community_keywords import (
    _attach_top_keywords,
    _build_synonym_map,
    _canonical_corpus,
    _community_id,
    _community_keywords_df,
    _community_sizes,
    _filtered_keywords_df,
    _keyword_scores_df,
    _labels_from_scores,
    _resolutions_in_graph,
    _scored_keywords_per_community,
    _split_keyword_field,
    resolutions,
)


def _tiny_graph() -> ig.Graph:
    g = ig.Graph(directed=True)
    g.add_vertices(5)
    g.vs["name"] = ["n0", "n1", "n2", "n3", "n4"]
    g.vs["keywords"] = [
        "Motor Learning|Cerebellum",
        "Motor Learning|Basal Ganglia, Striatum",
        "Reaching Movements",
        "",
        "Adaptation & Reaching",
    ]
    # Float-typed ids, as igraph reads them back from GraphML; -1 = no community.
    g.vs["cpm_communities_at_res=0.005"] = [0.0, 0.0, 1.0, 1.0, -1.0]
    g.vs["cpm_communities_at_res=0.01"] = [0.0, 1.0, 2.0, 2.0, 2.0]
    return g


# ── graph introspection ────────────────────────────────────────────────────────
def test_resolutions_in_graph_parses_and_sorts_attribute_names():
    assert _resolutions_in_graph(_tiny_graph()) == [0.005, 0.01]


def test_resolutions_node_requires_requested_resolutions_to_exist():
    g = _tiny_graph()
    assert resolutions(g, None) == [0.005, 0.01]
    assert resolutions(g, [0.01]) == [0.01]
    with pytest.raises(ValueError, match="not on the graph"):
        resolutions(g, [0.002])


def test_resolutions_node_rejects_graph_without_communities():
    g = ig.Graph(directed=True)
    g.add_vertices(2)
    with pytest.raises(ValueError, match="cpm_communities_at_res"):
        resolutions(g, None)


def test_community_id_coercion():
    assert _community_id(5.0) == 5
    assert _community_id("7") == 7
    assert _community_id(-1.0) is None
    assert _community_id(float("nan")) is None
    assert _community_id(None) is None
    assert _community_id("nope") is None


def test_community_sizes_counts_members_from_the_assignment_itself():
    assert _community_sizes(_tiny_graph(), 0.005) == {0: 2, 1: 2}
    assert _community_sizes(_tiny_graph(), 0.01) == {0: 1, 1: 1, 2: 3}


# ── keyword field splitting ───────────────────────────────────────────────────
def test_split_keyword_field_handles_dividers_ampersands_commas_and_missing():
    assert _split_keyword_field("Motor Learning|Basal Ganglia, Striatum", "|") == [
        "Motor Learning", "Basal Ganglia", "Striatum"]
    assert _split_keyword_field("Adaptation & Reaching", "|") == ["Adaptation", "Reaching"]
    assert _split_keyword_field("", "|") == []
    assert _split_keyword_field(None, "|") == []
    assert _split_keyword_field(float("nan"), "|") == []


# ── _build_synonym_map ────────────────────────────────────────────────────────
def test_build_synonym_map_maps_variants_to_lowercased_key():
    mapping = _build_synonym_map({"Motor Learning": ["Skill Acquisition", "MOTOR LEARNING"]})
    assert mapping["skill acquisition"] == "motor learning"
    assert mapping["motor learning"] == "motor learning"


# ── _filtered_keywords_df ─────────────────────────────────────────────────────
def test_filtered_keywords_df_splits_keywords_and_drops_unassigned_vertices():
    df = _filtered_keywords_df(_tiny_graph(), 0.005, "|", min_community_size=1)
    assert df.loc[df["community_id"] == 0, "keywords"].tolist() == [
        ["Motor Learning", "Cerebellum"],
        ["Motor Learning", "Basal Ganglia", "Striatum"],
    ]
    assert set(df["community_id"]) == {0, 1}          # the -1 vertex is gone
    assert df["community_size"].tolist() == [2, 2, 2, 2]


def test_filtered_keywords_df_drops_communities_below_min_size():
    df = _filtered_keywords_df(_tiny_graph(), 0.01, "|", min_community_size=2)
    assert set(df["community_id"]) == {2}


def test_filtered_keywords_df_without_keywords_attribute_is_empty_terms_not_crash():
    g = _tiny_graph()
    del g.vs["keywords"]
    df = _filtered_keywords_df(g, 0.005, "|", min_community_size=1)
    assert len(df) == 4 and all(kws == [] for kws in df["keywords"])


# ── _canonical_corpus ─────────────────────────────────────────────────────────
def test_canonical_corpus_rewrites_synonyms_and_joins_per_community():
    df = pd.DataFrame({"keywords": [["Motor Learning", "Cerebellum"], ["Skill Acquisition"]],
                       "community_id": [0, 0]})
    corpus = _canonical_corpus(df, {"skill acquisition": "motor learning"})
    assert corpus == {0: "motor learning\tcerebellum\tmotor learning"}


# ── scoring + labels ──────────────────────────────────────────────────────────
def test_scored_keywords_rank_distinguishing_terms_first_with_scores():
    corpus = {
        0: "shared\tshared\tcerebellum\tcerebellum\tcerebellum",
        1: "shared\tshared\tbasal ganglia\tbasal ganglia\tbasal ganglia",
    }
    scored = _scored_keywords_per_community(corpus, top_n=5)
    assert [kw for kw, _ in scored[0]][0] == "cerebellum"
    assert [kw for kw, _ in scored[1]][0] == "basal ganglia"
    # "shared" is in every document: corrected idf = log(2/2) = 0 -> score 0 -> dropped.
    assert "shared" not in dict(scored[0])
    assert all(isinstance(s, float) and s > 0 for _, s in scored[0])


def test_scored_keywords_drops_empty_documents_and_empty_corpus():
    assert set(_scored_keywords_per_community({0: "cerebellum", 1: ""}, top_n=3)) == {0}
    assert _scored_keywords_per_community({}, top_n=3) == {}


def test_labels_from_scores_title_cases_and_joins_top_n():
    scored = {0: [("motor learning", 0.9), ("cerebellum", 0.5), ("reaching", 0.1), ("x", 0.01)]}
    assert _labels_from_scores(scored, top_n_label=3) == {0: "Motor Learning; Cerebellum; Reaching"}
    assert _labels_from_scores({0: []}, top_n_label=3) == {}


# ── writing back onto the graph ───────────────────────────────────────────────
def test_attach_top_keywords_broadcasts_to_members_and_blanks_everything_else():
    g = _attach_top_keywords(_tiny_graph(), 0.005, {0: "Cerebellum; Motor Learning"})
    assert g.vs["top_keywords_at_res=0.005"] == [
        "Cerebellum; Motor Learning", "Cerebellum; Motor Learning", "", "", ""]


def test_graphml_roundtrip_keeps_labels_as_strings(tmp_path):
    g = _attach_top_keywords(_tiny_graph(), 0.005, {0: "Cerebellum; Motor Learning"})
    path = tmp_path / "g.graphml"
    g.write_graphml(str(path))
    back = ig.Graph.Read_GraphML(str(path))
    assert back.vs["top_keywords_at_res=0.005"] == [
        "Cerebellum; Motor Learning", "Cerebellum; Motor Learning", "", "", ""]


# ── tabular outputs ───────────────────────────────────────────────────────────
def test_community_keywords_df_one_row_per_resolution_and_community():
    df = _community_keywords_df(_tiny_graph(), {0.005: {0: "A; B", 1: "C"}, 0.01: {2: "D"}})
    assert len(df) == 3
    row = df[(df["resolution"] == 0.01) & (df["community_id"] == 2)].iloc[0]
    assert row["community_size"] == 3 and row["top_keywords"] == "D"


def test_keyword_scores_df_is_long_form_with_ranks():
    df = _keyword_scores_df({0.005: {0: [("cerebellum", 0.9), ("reaching", 0.4)]}})
    assert list(df.columns) == ["resolution", "community_id", "rank", "keyword", "corrected_tfidf_score"]
    assert df["rank"].tolist() == [1, 2]
    assert df["keyword"].tolist() == ["cerebellum", "reaching"]
