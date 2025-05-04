import importlib.metadata

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

try:
    VERSION = importlib.metadata.version("mcpgateway_client")
except importlib.metadata.PackageNotFoundError:
    VERSION = "unknown"

HEADER_OPTION = typer.Option(
    None,
    "--header",
    "-h",
    help="Add one or more headers (format: 'Key: Value'). Can be used multiple times. Ex: -h 'Authorization: Bearer token'",
    # multiple=True,
)

app = typer.Typer(
    help="CLI para executar comandos personalizados.",
    rich_help_panel="Comandos principais",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def show_version_and_exit() -> None:
    """Exibe versão usando Rich e sai."""
    console.print(
        Panel.fit(
            f"[bold green]SnapBy MCP Gateway:[/bold green] [cyan]CLIENT v{VERSION}[/cyan]\n\n"
            f"[bold red]https://snapby.com[/bold red]",
            title="[bold blue]Version[/bold blue]",
            border_style="green",
        )
    )
    raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        help="Exibe a versão do pacote e sai.",
        is_eager=True,
        callback=lambda value: show_version_and_exit() if value else None,
    ),
) -> None:
    """
    Main callback for the CLI application. Displays the main panel if no subcommand is invoked.

    Args:
        ctx (typer.Context): The Typer context object for the CLI invocation.

    Returns:
        None: This function does not return any value.
    """
    if ctx.invoked_subcommand is None and not version:
        console.print(
            Panel.fit(
                "[bold cyan]mcpgateway-client CLI[/bold cyan]\n\n"
                "[white]Use um dos comandos abaixo:[/white]\n"
                "[green]- foo[/green]: executa ação foo com dois argumentos obrigatórios\n"
                "[green]- bar[/green]: executa ação bar com dois argumentos obrigatórios\n\n"
                "[yellow]Dica:[/yellow] use '--help' após qualquer comando para mais detalhes.",
                title="[bold blue]SnapEnv CLI[/bold blue]",
                border_style="bright_blue",
            )
        )


@app.command(help="Executa o comando FOO com dois parâmetros obrigatórios.")
def foo(
    param1: str = typer.Argument(..., help="Primeiro parâmetro do comando FOO."),
    param2: str = typer.Argument(..., help="Segundo parâmetro do comando FOO."),
) -> None:
    """
    Executes the FOO command with two required parameters.

    Args:
        param1 (str): First parameter for the FOO command.
        param2 (str): Second parameter for the FOO command.

    Returns:
        None: This function does not return any value.
    """
    typer.echo(f"Executando FOO com param1={param1} e param2={param2}")


@app.command(help="Register your MCP Server to the Gateway.")
def register(
    gateway_url: str = typer.Option(
        ...,
        "--gateway-url",
        "-g",
        help="Gateway URL to connect to. Ex: ws://localhost:8765/mcp/register",
    ),
    gateway_auth_token: str = typer.Option(
        None,
        "--auth-token",
        "-a",
        help="Set the auth token for the gateway. Ex: Bearer abcdef123456",
    ),
    stdio: str = typer.Option(
        ...,
        "--stdio",
        "-i",
        help="Subprocess command to start. Ex: npx -y @modelcontextprotocol/server-filesystem ./",
    ),
    server_name: str = typer.Option(
        ...,
        "--server-name",
        "-n",
        help="Server name for registration. Ex: local-mcpserver-001",
    ),
    header: list[str] = HEADER_OPTION,
) -> None:
    """
    Executes the REGISTER command with required and optional parameters.

    Args:
        gateway_url (str): URL do gateway para conexão.
        gateway_auth_token (str, optional): Token de autenticação.
        stdio (str): Comando subprocesso.
        server_name (str): Nome do servidor.
        header (str, optional): Headers HTTP.

    Usage Examples:
        > cli register \\
            --gateway-url https://gateway.snapby.com \\
            --auth-token "Bearer abcdef123456" \\
            --stdio "/usr/local/bin/mcpserve" \\
            --server-name "snapby-server-001"

        > cli register -g https://localhost:8080 -i ./run.sh -n dev-env \\
            -h "Authorization: Bearer devtoken" -h "X-Custom-Header: test"

    Returns:
        None: Esta função não retorna valor.
    """
    typer.echo("Executando REGISTER:\n")
    typer.echo(f"Host: {gateway_url}")
    if gateway_auth_token:
        typer.echo(f"API Key: {gateway_auth_token}")
    else:
        typer.echo("Nenhuma API Key fornecida.")
    if header:
        typer.echo("Headers fornecidos:")
        for h in header:
            typer.echo(f"- {h}")
    else:
        typer.echo("Nenhum header fornecido.")
