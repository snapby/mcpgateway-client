import re

from typer.testing import CliRunner

from mcpgateway_client.cli import app

runner = CliRunner()


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_foo_ok():
    result = runner.invoke(app, ["foo", "arg1", "arg2"])
    output = strip_ansi(result.output.lower())
    assert result.exit_code == 0
    assert "param1=arg1" in output
    assert "param2=arg2" in output


def test_bar_missing_args():
    result = runner.invoke(app, ["bar", "alpha", "beta"])
    output = strip_ansi(result.output.lower())
    assert result.exit_code != 0
    # assert "--host" in result.output.lower()
    assert "--host" in output


def test_bar_ok_with_host():
    result = runner.invoke(app, ["bar", "alpha", "beta", "--host", "http://localhost"])
    output = strip_ansi(result.output.lower())
    assert result.exit_code == 0
    assert "host: http://localhost" in output


def test_bar_with_apikey():
    result = runner.invoke(app, ["bar", "a", "b", "--host", "http://x", "--apikey", "XYZ"])
    output = strip_ansi(result.output.lower())
    assert result.exit_code == 0
    assert "api key: xyz" in output


def test_bar_help():
    result = runner.invoke(app, ["bar", "--help"])
    output = strip_ansi(result.output.lower())
    assert result.exit_code == 0
    assert "executa o comando bar" in output
    assert "--host" in output
    assert "--apikey" in output
