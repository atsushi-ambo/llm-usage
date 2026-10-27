import pytest

from llm_usage.menubar_core import SIGNIN_ACTIONS, signin_action, terminal_applescript
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def provider(pid: ProviderId, *, reauth: bool) -> ProviderReport:
    return ProviderReport(
        provider=pid,
        display_name=pid.value,
        source=SourceKind.UNAVAILABLE,
        meta={"reauth_required": True} if reauth else {},
        errors=["auth rejected (401)"],
    )


@pytest.mark.parametrize(
    "pid, expected",
    [
        (ProviderId.CLAUDE, ("terminal", "claude /login")),
        (ProviderId.CODEX, ("terminal", "codex login")),
        (ProviderId.GROK, ("terminal", "grok login --oauth")),
        (ProviderId.MUSE, ("url", "https://muse.ai")),
    ],
)
def test_sign_in_required_offers_the_providers_login(pid, expected):
    assert signin_action(provider(pid, reauth=True)) == expected


def test_other_failures_do_not_offer_sign_in():
    assert signin_action(provider(ProviderId.CLAUDE, reauth=False)) is None


def test_terminal_script_runs_only_known_login_commands():
    args = terminal_applescript("codex login")
    assert args[0] == "osascript"
    assert 'do script "codex login"' in args
    with pytest.raises(ValueError):
        terminal_applescript('codex login"; rm -rf ~; "')


def test_every_action_is_a_fixed_safe_target():
    for kind, target in SIGNIN_ACTIONS.values():
        assert kind in {"terminal", "url"}
        if kind == "url":
            assert target.startswith("https://")
        else:
            assert '"' not in target and "\\" not in target


