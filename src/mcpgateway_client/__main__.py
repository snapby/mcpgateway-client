from rich.console import Console
from rich.panel import Panel

from mcpgateway_client.cli import app

console = Console()


def main() -> None:
    try:
        app()
    except Exception as e:
        console.print(
            Panel.fit(
                f"[bold red]Erro:[/bold red] {e!s}\n\n"
                "[yellow]Use '--help' para ver como usar o comando corretamente.[/yellow]",
                title="[red]Execução falhou[/red]",
                border_style="red",
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
