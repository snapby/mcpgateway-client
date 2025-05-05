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


def test_register_missing_args():
    result = runner.invoke(app, ["register", "alpha", "beta", "--dry-run"])
    output = strip_ansi(result.output.lower())
    assert result.exit_code != 0
    assert "--gateway-url" in output


def test_register_ok_with_host():
    result = runner.invoke(
        app,
        [
            "register",
            "--gateway-url",
            "ws://localhost:8765/mcp/register",
            "--stdio",
            "npx -y @modelcontextprotocol/server-filesystem ./",
            "--server-name",
            "local-mcpserver-001",
            "--dry-run",
        ],
    )
    output = strip_ansi(result.output.lower())
    assert result.exit_code == 0
    assert "gateway_url: ws://localhost:8765/mcp/register" in output


def test_register_with_apikey():
    result = runner.invoke(
        app,
        [
            "register",
            "--gateway-url",
            "ws://localhost:8765/mcp/register",
            "--auth-token",
            "Bearer abcdef123456",
            "--stdio",
            "npx -y @modelcontextprotocol/server-filesystem ./",
            "--server-name",
            "local-mcpserver-001",
            "--dry-run",
        ],
    )
    output = strip_ansi(result.output.lower())
    assert result.exit_code == 0
    assert "gateway_auth_token: bearer abcdef123456" in output


def test_register_help():
    result = runner.invoke(app, ["register", "--help"])
    output = strip_ansi(result.output)
    assert result.exit_code == 0
    assert "Register your MCP Server to the Gateway" in output
    assert "--gateway-url" in output
    assert "--auth-token" in output
