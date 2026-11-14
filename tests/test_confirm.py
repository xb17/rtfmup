"""T3.2 confirmation tests. Simulated stdin, no real prompts."""

from __future__ import annotations

import io

from rtfmup.execute.confirm import Confirmer, is_runnable, split_by_risk
from rtfmup.plan.model import Step


def step(id_: str, risk="low", **kw) -> Step:
    kw.setdefault("argv", ["echo", id_])
    return Step(id=id_, kind="setup", title=f"title {id_}", risk=risk, **kw)


def answers(*texts: str):
    it = iter(texts)

    def read(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return read


def make(*texts: str, assume_yes=False):
    out = io.StringIO()
    return Confirmer(assume_yes=assume_yes, read=answers(*texts), out=out), out


def test_split_by_risk_drops_unrunnable_steps():
    steps = [
        step("a"),
        step("b", "medium"),
        step("c", "high"),
        Step(id="m", kind="manual", title="by hand"),
        step("d", platform_ok=False),
    ]
    batch, high = split_by_risk(steps)
    assert [s.id for s in batch] == ["a", "b"]
    assert [s.id for s in high] == ["c"]
    assert not is_runnable(steps[3]) and not is_runnable(steps[4])


def test_batch_accepts_y_and_yes():
    for reply in ("y", "Y", "yes", " YES "):
        c, _ = make(reply)
        assert c.confirm_batch([step("a")])


def test_batch_default_is_no():
    for reply in ("", "n", "no", "maybe"):
        c, _ = make(reply)
        assert not c.confirm_batch([step("a")])


def test_batch_lists_commands_and_medium_risk_reasons():
    c, out = make("y")
    c.confirm_batch(
        [step("a"), step("b", "medium", risk_reasons=["installs globally"])]
    )
    text = out.getvalue()
    assert "echo a" in text and "echo b" in text
    assert "medium risk: installs globally" in text


def test_batch_with_nothing_to_run_does_not_prompt():
    c, _ = make()  # any read would raise EOFError and refuse
    assert c.confirm_batch([step("h", "high")])


def test_assume_yes_approves_batch_without_prompting():
    c, out = make(assume_yes=True)
    assert c.confirm_batch([step("a"), step("b", "medium")])
    assert "--yes: approved" in out.getvalue()


def test_high_risk_requires_the_full_word_yes():
    s = step("h", "high", risk_reasons=["needs admin"], argv=["choco", "install", "x"])
    c, out = make("yes")
    assert c.confirm_high_risk(s)
    shown = out.getvalue()
    assert "choco install x" in shown and "needs admin" in shown
    for reply in ("y", "Y", "ok", "", "yes please", "no"):
        c, _ = make(reply)
        assert not c.confirm_high_risk(s)


def test_assume_yes_never_approves_high_risk():
    c, out = make(assume_yes=True)  # reading would raise, so no prompt may happen
    assert not c.confirm_high_risk(step("h", "high"))
    assert "never approves high-risk" in out.getvalue()


def test_eof_and_ctrl_c_are_refusals():
    c, _ = make()  # immediate EOFError
    assert not c.confirm_batch([step("a")])
    assert not c.confirm_high_risk(step("h", "high"))

    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    c = Confirmer(read=interrupt, out=io.StringIO())
    assert not c.confirm_batch([step("a")])
    assert not c.confirm_high_risk(step("h", "high"))


def test_shell_text_step_shows_its_script():
    s = step("s", argv=None, shell_text="Copy-Item a b")
    c, out = make("y")
    assert c.confirm_batch([s])
    assert "Copy-Item a b" in out.getvalue()
