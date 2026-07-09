"""coverage_bucket normalizes rates; gaps are no-data, never zeros."""
import os

from safecross.storage import BUCKET_SEC, Store, bucket_key


def make_store(tmp_path):
    return Store(os.path.join(str(tmp_path), "stats.db"))


BASE = 1_800_000_000.0  # fixed epoch, minute-aligned enough for bucketing


def test_rate_normalized_by_observed_sec(tmp_path):
    st = make_store(tmp_path)
    # bucket A: fully observed, 10 pedestrians
    for i in range(6):
        st.add_coverage("x", BASE + i * 10, 10.0, "cam1")
    st.add_stat("x", BASE, "ped_crossed", 10)
    # bucket B (next minute): only half observed, 10 pedestrians
    b2 = BASE + 60
    for i in range(3):
        st.add_coverage("x", b2 + i * 10, 10.0, "cam1")
    st.add_stat("x", b2, "ped_crossed", 10)

    pts = {p.bucket_utc: p for p in st.rates("x", "ped_crossed")}
    full = pts[bucket_key(BASE)]
    half = pts[bucket_key(b2)]
    assert full.rate_per_hour == 10 * 3600 / 60.0   # 600/h
    assert half.rate_per_hour == 10 * 3600 / 30.0   # 1200/h — same count, half coverage
    st.close()


def test_gap_is_no_data_not_zero(tmp_path):
    st = make_store(tmp_path)
    # observed for 5s only (< 25% of bucket) and zero events
    st.add_coverage("x", BASE, 5.0, "cam1")
    p = st.rates("x", "ped_crossed")[0]
    assert p.value is None and p.rate_per_hour is None  # no-data, NOT 0
    assert p.observed_sec == 5.0
    st.close()


def test_zero_with_good_coverage_is_real_zero(tmp_path):
    st = make_store(tmp_path)
    st.add_coverage("x", BASE, BUCKET_SEC, "cam1")
    p = st.rates("x", "ped_crossed")[0]
    assert p.value == 0.0 and p.rate_per_hour == 0.0  # observed quiet minute
    st.close()


def test_stats_are_additive_upserts(tmp_path):
    st = make_store(tmp_path)
    st.add_coverage("x", BASE, 60.0, "cam1")
    st.add_stat("x", BASE + 1, "veh_passed", 2)
    st.add_stat("x", BASE + 30, "veh_passed", 3)
    p = st.rates("x", "veh_passed")[0]
    assert p.value == 5.0
    st.close()
