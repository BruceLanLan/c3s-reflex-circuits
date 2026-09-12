import json
from dataclasses import asdict
from pathlib import Path

import pytest

from c3s import calibrate, loom

ROOT = Path(__file__).resolve().parents[1]
SUBGRAPH = ROOT / "data" / "malecns-v1.0-gf-escape-subgraph.json"
CALIBRATED = calibrate.params_from_dict(json.loads((ROOT / "circuits" / "loom-escape" / "decision-table.json").read_text())["params"])


def test_onset_sample_is_exactly_the_start_size():
    # 10 degrees is also the first size-bin edge. Recomputed through tan and atan it
    # landed one ulp either side of that edge depending on the platform's libm.
    for lv in (8.0, 10.0, 40.0, 160.0):
        th, _ = loom.stimulus_samples(loom.Stimulus(lv, 0.0), CALIBRATED)[0]
        assert th == 10.0
        assert loom.DEFAULT_ENCODING.size_bin(th) == 1


def test_samples_grow_until_the_end_size():
    stim = loom.Stimulus(40.0, 0.0)
    samples = loom.stimulus_samples(stim, CALIBRATED)
    th = [s for s, _ in samples]
    dth = [d for _, d in samples]
    assert all(a < b for a, b in zip(th, th[1:]))
    assert all(a < b for a, b in zip(dth, dth[1:]))
    assert th[-1] < stim.end_deg


@pytest.mark.parametrize("lv_ms", [8.0, 40.0, 160.0])
def test_snapshot_recovers_l_over_v(lv_ms):
    a = lv_ms / 1000.0
    for t in (0.5, 0.05, 0.005):
        assert loom.l_over_v_from_snapshot(loom.theta_at(a, t), loom.dtheta_at(a, t)) == pytest.approx(a, rel=1e-9)


def test_expansion_speed_is_the_rate_of_size_change():
    a, t, h = 0.04, 0.1, 1e-6
    # size grows as time to contact shrinks
    numeric = (loom.theta_at(a, t - h) - loom.theta_at(a, t + h)) / (2 * h)
    assert loom.dtheta_at(a, t) == pytest.approx(numeric, rel=1e-6)


def test_visibility_overlaps_30_degrees_across_the_midline():
    assert loom.visible(0.0) == (True, True)
    assert loom.visible(-30.0) == (True, True)
    assert loom.visible(30.0) == (True, True)
    assert loom.visible(-30.5) == (True, False)
    assert loom.visible(30.5) == (False, True)


def test_encoding_zeroes_the_eye_that_cannot_see():
    enc = loom.DEFAULT_ENCODING
    b, m = enc.bits, enc.levels - 1
    left = loom.encode_features(60.0, 500.0, -60.0, enc)
    assert (left & m, left >> b & m) == (enc.size_bin(60.0), enc.speed_bin(500.0))
    assert left >> 2 * b == 0
    right = loom.encode_features(60.0, 500.0, 60.0, enc)
    assert right & ((1 << 2 * b) - 1) == 0
    assert right >> 2 * b == left


def test_teacher_with_explicit_samples_equals_teacher_with_geometry():
    w = loom.load_weights(SUBGRAPH, CALIBRATED)
    for stim in loom.family("train")[::5] + loom.family("holdout")[::7]:
        samples = loom.stimulus_samples(stim, CALIBRATED)
        assert loom.run_teacher_episode(stim, w, CALIBRATED) == loom.run_teacher_episode(stim, w, CALIBRATED, samples)


def test_params_round_trip_through_json():
    assert calibrate.params_from_dict(json.loads(json.dumps(asdict(CALIBRATED)))) == CALIBRATED
