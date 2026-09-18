# Qzhuli 扫码绑定 bind_key 清洗规则（hermes-dev 多 bot 独立绑定）。
# 回归：旧规则把非 [a-z0-9_] 全部替换为 '_'，不同中文名会被折叠成相同下划线串，
# 导致两个不同 bot 被服务端判为“同类型同名”；新规则保留中文，仅折叠空白与 '-'。

from hermes_cli.web_routers.messaging import _sanitize_qzhuli_bot_name


def test_sanitize_keeps_distinct_chinese_names_distinct():
    # 中文名必须保持可区分（修复“同长度中文名折叠成相同下划线串”的回归）
    a = _sanitize_qzhuli_bot_name("我的机器人")
    b = _sanitize_qzhuli_bot_name("你的机器人")
    assert a != b
    assert a == "我的机器人"
    assert b == "你的机器人"


def test_sanitize_never_emits_hyphen_and_folds_whitespace():
    # bind_key 以第一个 '-' 为 bot 名与随机串的分隔符，bot 名内不允许出现 '-'
    name = _sanitize_qzhuli_bot_name("sophia-dev")
    assert name == "sophia_dev"
    assert "-" not in _sanitize_qzhuli_bot_name("  my  bot  ")
    assert _sanitize_qzhuli_bot_name("  my  bot  ") == "my_bot"
    assert _sanitize_qzhuli_bot_name("Sophia") == "sophia"


def test_sanitize_falls_back_to_hermes_on_empty():
    assert _sanitize_qzhuli_bot_name(None) == "hermes"
    assert _sanitize_qzhuli_bot_name("") == "hermes"
    assert _sanitize_qzhuli_bot_name("   ") == "hermes"


def test_sanitize_degenerate_input_never_emits_hyphen_or_empty():
    for value in ("-", "---", "  - "):
        out = _sanitize_qzhuli_bot_name(value)
        assert "-" not in out
        assert out
