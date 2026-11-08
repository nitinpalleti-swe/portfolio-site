"""The eval bypass must only work in development, from the local socket, with the right token."""

from types import SimpleNamespace

import pytest

import app


def req(token="", host="127.0.0.1", model=""):
    headers = {"x-eval-token": token, "x-eval-model": model}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


@pytest.fixture(autouse=True)
def dev_with_token(monkeypatch):
    monkeypatch.setattr(app, "DEV", True)
    monkeypatch.setattr(app, "EVAL_TOKEN", "secret-eval-token")


def test_valid_local_token_is_eval():
    assert app.eval_mode(req("secret-eval-token"))


@pytest.mark.parametrize("token, host", [
    ("", "127.0.0.1"),                  # no token
    ("wrong", "127.0.0.1"),             # wrong token
    ("secret-eval-token", "203.0.113.7"),  # right token, not from localhost
])
def test_rejected(token, host):
    assert not app.eval_mode(req(token, host))


def test_rejected_outside_development(monkeypatch):
    monkeypatch.setattr(app, "DEV", False)
    assert not app.eval_mode(req("secret-eval-token"))


def test_rejected_when_no_eval_token_configured(monkeypatch):
    monkeypatch.setattr(app, "EVAL_TOKEN", "")
    assert not app.eval_mode(req(""))


@pytest.mark.parametrize("name, expected", [
    ("claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("", "claude-haiku-5-5"),
    ("bad name; rm -rf", "claude-haiku-5-5"),
])
def test_eval_model_override(name, expected):
    assert app.eval_model(req(model=name), "x-eval-model", "claude-haiku-5-5") == expected
