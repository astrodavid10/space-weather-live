"""parse_iso_z / unix_s / age_hours -- the timezone contract.

These tests only mean something on a machine whose local zone is NOT UTC, and
that is the point: the bug they pin (a zone-less SWPC ``time_tag`` read as
local time, shipping wind samples 5 h in the future) is invisible on a UTC
runner and was found on a Central-time workstation.  ``test_local_zone_differs``
records whether the environment can exercise it rather than skipping silently.
"""

from datetime import datetime, timedelta, timezone

from ..io_utils import age_hours, iso_z, parse_iso_z, unix_s

BARE = "2026-09-02T15:23:00"
WITH_Z = "2026-09-02T15:23:00Z"
WITH_OFFSET = "2026-09-02T15:23:00+00:00"


def test_all_three_spellings_are_tz_aware():
    for s in (BARE, WITH_Z, WITH_OFFSET):
        dt = parse_iso_z(s)
        assert dt is not None, s
        assert dt.tzinfo is not None, s
        assert dt.utcoffset() == timedelta(0), s


def test_all_three_spellings_agree_on_unix():
    got = {s: unix_s(parse_iso_z(s)) for s in (BARE, WITH_Z, WITH_OFFSET)}
    assert len(set(got.values())) == 1, got
    # And it is the RIGHT instant, not merely a consistent one.
    assert got[BARE] == int(datetime(2026, 9, 2, 15, 23,
                                     tzinfo=timezone.utc).timestamp())


def test_bare_string_round_trips_through_iso_z():
    assert iso_z(parse_iso_z(BARE)) == WITH_Z


def test_age_hours_on_a_bare_string_does_not_raise():
    # Pre-fix this raised TypeError ("can't subtract offset-naive and
    # offset-aware datetimes") or silently reported a 5 h error, depending on
    # which side of the subtraction the naive value landed on.
    now = datetime(2026, 9, 2, 16, 23, tzinfo=timezone.utc)
    assert abs(age_hours(parse_iso_z(BARE), now) - 1.0) < 1e-9
    # Same instant, both spellings, against ONE explicit clock: the first CI
    # run of this suite compared two calls that each read utcnow() and the
    # 4 us between them was a 1.1e-9 h difference -- a flake, not a bug.
    assert age_hours(parse_iso_z(BARE), now) == age_hours(parse_iso_z(WITH_Z), now)
    # And with the implicit clock, only that it does not raise.
    assert age_hours(parse_iso_z(BARE)) > 0


def test_offset_bearing_strings_are_not_forced_to_utc():
    """The fix must only fill in a MISSING offset, never override one."""
    dt = parse_iso_z("2026-09-02T15:23:00-05:00")
    assert dt is not None and dt.utcoffset() == timedelta(hours=-5)
    assert unix_s(dt) == unix_s(parse_iso_z("2026-09-02T20:23:00Z"))


def test_garbage_and_empty_still_return_none():
    assert parse_iso_z("") is None
    assert parse_iso_z("not a timestamp") is None


def test_local_zone_differs():
    """Informational: is this machine able to catch the original bug at all?"""
    local_offset = datetime.now().astimezone().utcoffset()
    if local_offset == timedelta(0):
        print("NOTE: local zone is UTC; the naive-as-local bug is "
              "unobservable here")
    assert True
