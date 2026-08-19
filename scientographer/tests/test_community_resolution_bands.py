"""Locks the shared resolution sweep every community-analysis DAG imports.

The band lists must exactly reproduce the stored ``cpm_communities_at_res=<r>``
column names, and cover all three graphml bands with no gaps or duplicates -- a
drift here would silently truncate every downstream metric plot.
"""

from motor_learning_network.community_resolution_bands import (
    LOW_BAND,
    MID_BAND,
    HIGH_BAND,
    RESOLUTIONS,
    community_attribute_name,
)


def test_full_sweep_is_three_bands_36_ascending_unique():
    assert LOW_BAND == [round(i * 0.001, 3) for i in range(1, 10)]
    assert MID_BAND == [round(i * 0.01, 2) for i in range(1, 20)]
    assert HIGH_BAND == [round(i * 0.1, 1) for i in range(2, 10)]
    assert RESOLUTIONS == LOW_BAND + MID_BAND + HIGH_BAND
    assert len(RESOLUTIONS) == 36
    assert len(set(RESOLUTIONS)) == 36
    assert RESOLUTIONS == sorted(RESOLUTIONS)
    assert RESOLUTIONS[0] == 0.001 and RESOLUTIONS[-1] == 0.9


def test_attribute_name_matches_stored_column_strings():
    # These are the exact strings present as vertex attributes / parquet columns;
    # float repr must not gain trailing zeros (0.1 not 0.10, 0.2 not 0.20).
    assert community_attribute_name(0.001) == "cpm_communities_at_res=0.001"
    assert community_attribute_name(0.01) == "cpm_communities_at_res=0.01"
    assert community_attribute_name(0.1) == "cpm_communities_at_res=0.1"
    assert community_attribute_name(0.2) == "cpm_communities_at_res=0.2"
    assert community_attribute_name(0.9) == "cpm_communities_at_res=0.9"
    # every swept resolution formats without a trailing-zero artifact
    for r in RESOLUTIONS:
        assert community_attribute_name(r).endswith(str(r))


def test_bands_do_not_overlap():
    assert set(LOW_BAND).isdisjoint(MID_BAND)
    assert set(LOW_BAND).isdisjoint(HIGH_BAND)
    assert set(MID_BAND).isdisjoint(HIGH_BAND)
