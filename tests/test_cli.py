from typer.testing import CliRunner

from mcpgateway_client.cli import app

runner = CliRunner()


def test_foo_ok():
    result = runner.invoke(app, ["foo", "arg1", "arg2"])
    assert result.exit_code == 0
    assert "param1=arg1" in result.output
    assert "param2=arg2" in result.output


def test_bar_missing_args():
    result = runner.invoke(app, ["bar", "alpha", "beta"])
    assert result.exit_code != 0
    assert "--host" in result.output.lower()


def test_bar_ok_with_host():
    result = runner.invoke(app, ["bar", "alpha", "beta", "--host", "http://localhost"])
    assert result.exit_code == 0
    assert "Host: http://localhost" in result.output


def test_bar_with_apikey():
    result = runner.invoke(app, ["bar", "a", "b", "--host", "http://x", "--apikey", "XYZ"])
    assert result.exit_code == 0
    assert "API Key: XYZ" in result.output


def test_bar_help():
    result = runner.invoke(app, ["bar", "--help"])
    assert result.exit_code == 0
    assert "Executa o comando BAR" in result.output
    assert "--host" in result.output
    assert "--apikey" in result.output
