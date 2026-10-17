"""Initial profile files, written on the staging tree before publication."""
import contextlib
from pathlib import Path
from typing import Optional


def finish_profile_layout(profile_dir: Path, *, no_skills: bool, clone_all: bool,
                           description: Optional[str], soul: Optional[str] = None) -> None:
    """Seed files a fresh profile owns from day one; runs on the staging tree before publish."""
    from hermes_cli.profiles import (
        _seed_file_if_missing, _PLACEHOLDER_ENV, NO_BUNDLED_SKILLS_MARKER,
        _migrate_profile_config_if_outdated, write_profile_meta,
        _seed_qzhuli_channel, _seed_model_auth,
    )
    if soul is not None:
        (profile_dir / "SOUL.md").write_text(soul, encoding="utf-8")

    # Seed an empty .env so the profile owns a credentials file from day one. Without it,
    # profile-scoped env writes (dashboard Channels/Keys pages, `hermes -p <name> auth add`)
    # had no file until first write and the profile silently inherited shell API keys —
    # read by users as "the new profile reads the root .env". Skipped when a clone copied one.
    _seed_file_if_missing(profile_dir / ".env", _PLACEHOLDER_ENV, 0o600)

    # hermes-dev: qzhuli (Q助理) 是默认消息通道——新 profile 自动带上插件目录和 platforms
    # 启用配置（不复制任何凭据/绑定，通道凭据仍走各自的扫码绑定流程）。
    _seed_qzhuli_channel(profile_dir)

    # hermes-dev: 新 profile 继承默认 profile 的模型登录（providers），否则每次新建 bot
    # 都得手动重设登录（`auth.json` 的 providers 为空 → "Hermes is not logged into Nous
    # Portal"）。只继承 providers（模型 provider 登录是共享进程凭据），绝不复制消息通道
    # bot token/凭据——通道凭据走各自的扫码绑定。
    _seed_model_auth(profile_dir)

    # Default SOUL.md to customize immediately (skipped when a clone already provided one).
    with contextlib.suppress(Exception):  # best-effort — don't fail profile creation over this
        from hermes_cli.default_soul import DEFAULT_SOUL_MD
        _seed_file_if_missing(profile_dir / "SOUL.md", DEFAULT_SOUL_MD)

    # Opt-out marker read by seed_profile_skills() and `hermes update`'s all-profile sync
    # (the feature still works via the empty skills/ dir if this fails).
    if no_skills:
        _seed_file_if_missing(
            profile_dir / NO_BUNDLED_SKILLS_MARKER,
            "This profile opted out of bundled-skill seeding (`hermes profile create --no-skills`).\n"
            "Delete this file to re-enable sync on the next `hermes update`.\n",
        )

    # Migrate config-only clones now so desktop/status don't warn that a just-created
    # profile is v0/outdated; --clone-all snapshots stay byte-for-byte apart from the
    # explicit runtime/history stripping above.
    if not clone_all:
        _migrate_profile_config_if_outdated(profile_dir)

    # Description last, so a partial-create failure doesn't strand a description file.
    if description and description.strip():
        with contextlib.suppress(Exception):  # non-fatal — `hermes profile describe` works later
            write_profile_meta(profile_dir, description=description.strip(), description_auto=False)
