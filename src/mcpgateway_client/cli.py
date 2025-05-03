import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

app = typer.Typer(
    help="CLI para executar comandos personalizados.",
    rich_help_panel="Comandos principais",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
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
    typer.echo(f"Executando FOO com param1={param1} e param2={param2}")


@app.command(help="Executa o comando BAR com dois parâmetros obrigatórios.")
def bar(
    param1: str = typer.Argument(..., help="Primeiro parâmetro do comando BAR."),
    param2: str = typer.Argument(..., help="Segundo parâmetro do comando BAR."),
    host: str = typer.Option(..., "--host", "-h", help="Endereço do host para conexão (obrigatório)."),
    apikey: str = typer.Option(None, "--apikey", help="Chave de API opcional para autenticação."),
) -> None:
    typer.echo(f"Executando BAR com param1={param1}, param2={param2}")
    typer.echo(f"Host: {host}")
    if apikey:
        typer.echo(f"API Key: {apikey}")
    else:
        typer.echo("Nenhuma API Key fornecida.")
