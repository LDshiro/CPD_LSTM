from pathlib import Path

from cpdshadow.instruments import load_instrument_master


EXPECTED_ROOTS = {
    "ES", "NQ", "RTY", "YM",
    "ZT", "ZF", "ZN", "ZB",
    "6E", "6J", "6B", "6A",
    "GC", "SI", "HG",
    "CL", "NG",
    "ZC", "ZW", "ZS",
}


def test_load_instrument_master() -> None:
    master = load_instrument_master(Path("config/instruments.yml"))
    roots = {instrument.root for instrument in master.instruments}
    assert roots == EXPECTED_ROOTS
    assert master.policy.standard_contracts_are_primary is True
    assert master.policy.auto_micro_substitution_enabled is False
    assert len(master.instruments) == 20


def test_tick_values_are_self_consistent() -> None:
    master = load_instrument_master(Path("config/instruments.yml"))
    for instrument in master.instruments:
        expected = instrument.min_price_increment * instrument.quote_multiplier_to_usd_notional
        assert abs(expected - instrument.tick_value_usd) < 1e-9


def test_restricted_and_none_micro_policy_is_explicit() -> None:
    master = load_instrument_master(Path("config/instruments.yml"))
    lookup = {instrument.root: instrument for instrument in master.instruments}

    assert lookup["ES"].micro_substitute.root == "MES"
    assert lookup["ES"].micro_substitute.status == "approved"

    assert lookup["6J"].micro_substitute.root == "MJY"
    assert lookup["6J"].micro_substitute.status == "restricted"

    assert lookup["ZN"].micro_substitute.status == "none"
    assert lookup["ZN"].micro_substitute.available is False
