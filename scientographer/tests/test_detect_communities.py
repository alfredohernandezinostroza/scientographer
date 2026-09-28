import igraph as ig

from motor_learning_network.detect_communities import (
    _attach_memberships,
    _filter_by_degree,
    _leiden_cpm_membership,
    _partition_summary,
)


def _two_cliques() -> ig.Graph:
    """Two 5-cliques joined by one edge, plus an isolated-ish pendant vertex."""
    g = ig.Graph(directed=True)
    g.add_vertices(11)
    g.vs["name"] = [f"n{i}" for i in range(11)]
    edges = []
    for base in (0, 5):
        for i in range(5):
            for j in range(5):
                if i < j:
                    edges.append((base + i, base + j))
    edges.append((4, 5))       # the bridge
    edges.append((10, 0))      # pendant
    g.add_edges(edges)
    return g


def test_filter_by_degree_keeps_everything_by_default_and_returns_a_copy():
    g = _two_cliques()
    same = _filter_by_degree(g, 0)
    assert same.vcount() == g.vcount() and same is not g


def test_filter_by_degree_drops_low_degree_vertices():
    g = _two_cliques()
    filtered = _filter_by_degree(g, 2)
    assert filtered.vcount() == 10 and "n10" not in filtered.vs["name"]


def test_leiden_cpm_finds_the_two_cliques_and_numbers_by_size():
    g = _two_cliques()
    membership = _leiden_cpm_membership(g, resolution=0.3, seed=0, n_iterations=5)
    assert len(membership) == 11
    assert len(set(membership[0:5])) == 1 and len(set(membership[5:10])) == 1
    assert membership[0] != membership[5]
    # Largest community first: the two cliques (5 members, or 6 with the pendant) get 0/1.
    assert set(membership[0:10]) == {0, 1}


def test_leiden_is_reproducible_for_a_seed():
    g = _two_cliques()
    a = _leiden_cpm_membership(g, 0.3, seed=7, n_iterations=3)
    b = _leiden_cpm_membership(g, 0.3, seed=7, n_iterations=3)
    assert a == b


def test_attach_memberships_writes_one_column_per_resolution():
    g = _two_cliques()
    g = _attach_memberships(g, {0.3: [0] * 11, 0.5: list(range(11))})
    assert g.vs["cpm_communities_at_res=0.3"] == [0] * 11
    assert g.vs["cpm_communities_at_res=0.5"] == list(range(11))


def test_partition_summary_counts_singletons_and_largest():
    g = _two_cliques()
    g = _attach_memberships(g, {0.3: [0] * 5 + [1] * 5 + [2]})
    df = _partition_summary(g, [0.3])
    row = df.iloc[0]
    assert row["number_of_communities"] == 3
    assert row["number_of_singleton_communities"] == 1
    assert row["largest_community_size"] == 5
    assert row["nodes_in_communities_with_at_least_2_members"] == 10
