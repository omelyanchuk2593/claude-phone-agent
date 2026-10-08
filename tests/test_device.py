import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import device  # noqa: E402
from device import Snapshot, owner_verdict, parse_idle_seconds, parse_locked, parse_probe  # noqa: E402

XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" package="com.instagram.android" bounds="[0,0][1080,2400]">
    <node resource-id="com.instagram.android:id/action_bar_title" class="android.widget.TextView"
          text="demo.account" package="com.instagram.android" bounds="[40,100][400,160]" />
    <node resource-id="com.instagram.android:id/button_container" class="android.widget.Button"
          content-desc="Message" clickable="true" package="com.instagram.android" bounds="[560,600][1040,700]" />
    <node resource-id="com.instagram.android:id/profile_header_follow_button" class="android.widget.Button"
          text="Follow" clickable="true" package="com.instagram.android" bounds="[40,600][520,700]" />
    <node class="android.widget.Button" text="Unfollow" clickable="true"
          package="com.instagram.android" bounds="[40,800][520,900]" />
    <node resource-id="com.instagram.android:id/row_thread_composer_edittext" class="android.widget.EditText"
          text="" focused="true" clickable="true" package="com.instagram.android" bounds="[40,2200][900,2300]" />
    <node class="android.view.View" package="com.instagram.android" bounds="[0,0][0,0]" text="invisible" />
    <node class="android.view.View" package="com.instagram.android" bounds="[0,1000][1080,1100]" />
  </node>
</hierarchy>"""


def test_snapshot_keeps_only_meaningful_visible_nodes():
    snap = Snapshot(XML)
    assert (snap.width, snap.height) == (1080, 2400)
    ids = [n.short_id for n in snap.items]
    assert "action_bar_title" in ids and "button_container" in ids
    texts = [n.text for n in snap.items]
    assert "invisible" not in texts  # нулевая площадь
    # пустой некликабельный контейнер не показываем
    assert not any(n.cls == "android.view.View" for n in snap.items)


def test_find_prefers_exact_match_and_filters_by_id():
    snap = Snapshot(XML)
    assert [n.text for n in snap.find(text="follow")] == ["Follow"]  # не Unfollow
    assert [n.short_id for n in snap.find(desc="Message")] == ["button_container"]
    assert snap.find(rid="row_thread_composer_edittext")[0].editable


def test_danger_buttons_detected():
    snap = Snapshot(XML)
    by_text = {n.text: n for n in snap.items if n.text}
    assert by_text["Unfollow"].dangerous()
    assert not by_text["Follow"].dangerous()


def test_identity_survives_json_roundtrip_and_detects_moves():
    snap = Snapshot(XML)
    node = snap.find(desc="Message")[0]
    ident = node.identity()
    assert node.same_as(ident)
    moved = dict(ident, bounds=[560, 650, 1040, 750])
    assert not node.same_as(moved)
    assert node.same_as(moved, with_bounds=False)


def test_line_format_is_compact():
    snap = Snapshot(XML)
    line = snap.find(desc="Message")[0].line(3)
    assert line.startswith("[3] Button (desc: Message) id=button_container @800,650")
    assert "[click]" in line


def test_idle_seconds_both_forms_take_freshest():
    power = ("mLastUserActivityTime(excludingAttention)=19067928\n"
             "lastUserActivityTime=19067928 (72912 ms ago)\n"
             "mLastUserActivityTimeNoChangeLights=+1m5s ago\n")
    assert parse_idle_seconds(power) == 65
    assert parse_idle_seconds("mLastUserActivityTime=+42s ago") == 42
    assert parse_idle_seconds("mLastUserActivityTime=19067928") is None


def test_locked_true_wins_and_unknown_is_none():
    assert parse_locked("mShowingLockscreen=false mDreamingLockscreen=true")[0] is True
    assert parse_locked("mKeyguardShowing=false")[0] is False
    assert parse_locked("nothing here")[0] is None


def test_probe_parses_sections_separately():
    out = ("  mWakefulness=Awake\n  lastUserActivityTime=1 (5000 ms ago)\n@@@\n"
           "    mKeyguardShowing=false\n@@@\n"
           "  mCurrentFocus=Window{1b2c3d u0 com.instagram.android/com.instagram.mainactivity.MainActivity}\n@@@\n"
           "  AC powered: false\n  USB powered: true\n  level: 81\n")
    st = parse_probe(out)
    assert st["screen_on"] is True and st["locked"] is False and st["idle"] == 5
    assert st["focus"] == "com.instagram.android"
    assert st["activity"] == "com.instagram.mainactivity.MainActivity"
    assert st["battery"] == 81 and st["charging"] is True


def _state(**kw):
    base = {"screen_on": True, "locked": False, "lock_note": "", "idle": 300}
    base.update(kw)
    return base


def test_owner_verdict():
    need = 45
    assert owner_verdict(_state(), None, need) is None
    assert "в руках" in owner_verdict(_state(idle=5), None, need)
    # последняя активность совпадает с нашим касанием — это мы, не владелец
    assert owner_verdict(_state(idle=5), 8.0, need) is None
    # владелец тронул после нас
    assert "в руках" in owner_verdict(_state(idle=5), 120.0, need)
    assert "заблокирован" in owner_verdict(_state(locked=True), None, need)
    assert owner_verdict(_state(locked=None, lock_note="x"), None, need) is not None
    assert "измерить" in owner_verdict(_state(idle=None), None, need)
    assert owner_verdict(_state(screen_on=False, idle=1), None, need) is None


def test_lease_one_session_at_a_time(tmp_path):
    lease = tmp_path / "lease.json"
    device.claim_phone("claude-server", path=lease, now=1000)
    device.claim_phone("claude-server", path=lease, now=1010)  # своя сессия — можно
    try:
        device.claim_phone("claude-laptop", path=lease, now=1030)
        assert False, "чужая сессия не должна перехватить телефон"
    except device.PhoneTaken as e:
        assert "claude-server" in str(e)
    # после тишины дольше аренды телефон свободен
    device.claim_phone("claude-laptop", path=lease, now=1010 + device.LEASE_SECONDS + 1)


def test_config_defaults_present():
    cfg = device.load_config()
    for key in ("address", "owner_idle_seconds", "tap_pause", "shot_width"):
        assert key in cfg
