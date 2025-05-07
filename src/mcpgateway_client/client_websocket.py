#!/usr/bin/env python3
"""
MCP (Model Context Protocol) stdio-to-ws Gateway - Modified Version
"""

import asyncio
import contextlib
import json
import logging
import signal
import ssl
import sys
import uuid
from collections.abc import Awaitable
from typing import Any, Callable, Optional

import websockets.client

# from websockets.client import WebSocketClientProtocol
# from websockets.client import ClientConnection
from mcpgateway_client.types import StdioToWsArgs

logger = logging.getLogger(__name__)

_cleanup_started_event = asyncio.Event()
_cleanup_lock = asyncio.Lock()

MCP_ANNOUNCED_PROTOCOL_VERSION = "2024-11-05"


class GatewayClient:
    """Gateway client that handles communication with the upstream gateway"""

    def __init__(
        self,
        gateway_url: str,
        server_name: str,
        server_id: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self.gateway_url = gateway_url
        self.server_name = server_name
        self.server_id = server_id or str(uuid.uuid4())
        # self.websocket: Optional[ClientConnection] = None
        self.websocket: Optional[websockets.ClientConnection] = None
        self.message_handlers: dict[str, Callable[[Any, Optional[str]], Awaitable[None]]] = {}
        self.is_connected = False
        self.headers = headers or {}
        self.ssl_context = ssl_context
        self._listen_task: Optional[asyncio.Task] = None

    def add_message_handler(self, name: str, handler: Callable[[Any, Optional[str]], Awaitable[None]]) -> None:
        self.message_handlers[name] = handler

    async def send(self, message: Any) -> None:
        if not self.websocket or not self.is_connected:
            logger.debug("Gateway not connected, unable to send message.")
            return

        message_str = json.dumps(message)
        try:
            await self.websocket.send(message_str)
            logger.debug(f"Message sent to gateway: {message_str[:100]}...")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Failed to send message: Gateway connection closed.")
            self.is_connected = False
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to send message to gateway")
            # self.is_connected = False # Potentially, but ConnectionClosed is more specific

    # Dentro da classe GatewayClient
    async def _request_stdio_subprocess(  # noqa: C901
        self,
        method_name: str,
        params: dict,
        proc_stdin: Optional[asyncio.StreamWriter],
        pending_stdio_requests: dict[Any, asyncio.Future],
        loop: asyncio.AbstractEventLoop,
        timeout_sec: float = 15.0,
    ) -> Any:
        if not proc_stdin or proc_stdin.is_closing():
            logger.error(f"Subprocess stdin not available for sending request '{method_name}'.")
            raise OSError(f"Subprocess stdin not available for '{method_name}'.")  # noqa: TRY003

        stdio_req_id = str(uuid.uuid4())
        future = loop.create_future()
        pending_stdio_requests[stdio_req_id] = future

        request_to_stdio_payload = {
            "jsonrpc": "2.0",
            "id": stdio_req_id,
            "method": method_name,
            "params": params,
        }
        request_str = json.dumps(request_to_stdio_payload) + "\n"
        logger.debug(f"Client (proxy) -> STDIO (Req ID: {stdio_req_id}, Method: {method_name}): {request_str[:200]}...")

        try:
            proc_stdin.write(request_str.encode("utf-8"))
            await proc_stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as e:
            logger.error(f"Pipe error writing to subprocess stdin for {stdio_req_id} ('{method_name}'): {e}")  # noqa: TRY400
            if stdio_req_id in pending_stdio_requests:  # Garante que só remove se existir
                pending_stdio_requests.pop(stdio_req_id)
            if not future.done():
                future.set_exception(e)
            raise
        except Exception as e_write:
            logger.exception(f"Unexpected error writing to subprocess stdin for {stdio_req_id} ('{method_name}')")
            if stdio_req_id in pending_stdio_requests:
                pending_stdio_requests.pop(stdio_req_id)
            if not future.done():
                future.set_exception(e_write)
            raise

        try:
            logger.debug(
                f"Waiting for STDIO response for internal req ID {stdio_req_id} ('{method_name}', Timeout: {timeout_sec}s)"
            )
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.error(  # noqa: TRY400
                f"Timeout waiting for STDIO response for method '{method_name}', internal req ID '{stdio_req_id}'."
            )
            if stdio_req_id in pending_stdio_requests:  # Limpa se timeout
                pending_stdio_requests.pop(stdio_req_id)
            raise
        except Exception as e_await:
            logger.debug(
                f"Error from future for STDIO method '{method_name}', internal req ID '{stdio_req_id}': {e_await}"
            )
            # pending_stdio_requests deve ter sido removido se set_exception foi chamado em read_stdout
            raise

    async def _internal_listen_loop(self) -> None:  # noqa: C901
        """Internal loop to continuously listen for messages."""
        try:
            if not self.websocket:  # Should not happen if called correctly
                logger.error("WebSocket not available for listening.")
                return

            logger.debug("Starting internal listen loop for gateway messages.")
            async for message_raw in self.websocket:
                message_str = str(message_raw)[:200]  # Limit log size
                logger.info(f"Received gateway message: {message_str}...")

                try:
                    msg_data = json.loads(message_raw)
                    msg_id = msg_data.get("id") if isinstance(msg_data, dict) else None
                    for handler_name, handler_func in self.message_handlers.items():
                        # Schedule handler execution to not block the listen loop
                        asyncio.create_task(  # noqa: RUF006
                            handler_func(msg_data, msg_id),  # type: ignore[arg-type]
                            name=f"gw_handler_{handler_name}_{msg_id or 'no_id'}",
                        )
                except json.JSONDecodeError:
                    logger.exception(f"Received invalid JSON from gateway: {message_raw!r}")
                except Exception:  # pylint: disable=broad-except
                    logger.exception("Error processing gateway message in handler")
        except websockets.exceptions.ConnectionClosedOK:
            logger.info("Gateway connection closed normally (OK).")
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"Gateway connection closed with error: {e.code} {e.reason}")
        except websockets.exceptions.ConnectionClosed as e:  # Catch-all
            logger.warning(f"Gateway connection closed unexpectedly: {e!r}")
        except asyncio.CancelledError:
            logger.info("Gateway listening loop was cancelled.")
        except Exception:  # pylint: disable=broad-except
            logger.exception("Unhandled exception in gateway listening loop.")
        finally:
            self.is_connected = False
            logger.debug("Gateway internal listen loop finished.")

    # Dentro da classe GatewayClient
    async def _handle_server_request_during_handshake(
        self,
        request_data: dict,
        proc_stdin: Optional[asyncio.StreamWriter],
        pending_stdio_requests: dict[Any, asyncio.Future],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        method = request_data.get("method")
        remote_gateway_req_id = request_data.get("id")
        response_payload = None
        # ... (definições de client_name, client_version)
        client_name = "mcpgateway-client-stdio"
        client_version = "0.1.0"

        if method == "initialize":
            # ... (como antes, usando "2024-11-05" para protocolVersion) ...
            logger.info(f"Responding to 'initialize' request (ID: {remote_gateway_req_id}) from gateway.")
            client_announced_protocol_version = "2024-11-05"
            response_payload = {
                "jsonrpc": "2.0",
                "id": remote_gateway_req_id,
                "result": {
                    "protocolVersion": client_announced_protocol_version,
                    "serverInfo": {"name": client_name, "version": client_version},
                    "capabilities": {},
                },
            }
        elif method == "tools/list":
            logger.info(
                f"Received 'tools/list' (ID: {remote_gateway_req_id}) from remote gateway. Querying stdio subprocess."
            )
            if not proc_stdin:
                logger.error("Cannot query stdio for tools/list: proc_stdin is None.")
                response_payload = {
                    "jsonrpc": "2.0",
                    "id": remote_gateway_req_id,
                    "error": {"code": -32000, "message": "Internal error: stdio not available"},
                }
            else:
                try:
                    stdio_result_content = await self._request_stdio_subprocess(
                        "tools/list", {}, proc_stdin, pending_stdio_requests, loop, timeout_sec=20.0
                    )
                    if isinstance(stdio_result_content, dict) and "tools" in stdio_result_content:
                        response_payload = {
                            "jsonrpc": "2.0",
                            "id": remote_gateway_req_id,
                            "result": stdio_result_content,
                        }
                        logger.info(
                            f"Responding to remote gateway's 'tools/list' (ID: {remote_gateway_req_id}) with {len(stdio_result_content['tools'])} tools from stdio."
                        )
                    else:
                        logger.error(
                            f"STDIO server provided invalid or unexpected result for tools/list: {stdio_result_content}"
                        )
                        response_payload = {
                            "jsonrpc": "2.0",
                            "id": remote_gateway_req_id,
                            "error": {"code": -32002, "message": "Invalid response from stdio server for tools/list"},
                        }
                except asyncio.TimeoutError:
                    logger.error(  # noqa: TRY400
                        f"Timeout querying stdio subprocess for 'tools/list' (for remote gateway request ID {remote_gateway_req_id})."
                    )
                    response_payload = {
                        "jsonrpc": "2.0",
                        "id": remote_gateway_req_id,
                        "error": {"code": -32000, "message": "Timeout obtaining tools from stdio server"},
                    }
                except Exception as e:
                    logger.exception(
                        f"Error querying stdio subprocess for 'tools/list' (for remote gateway request ID {remote_gateway_req_id}): {e}"  # noqa: TRY401
                    )
                    response_payload = {
                        "jsonrpc": "2.0",
                        "id": remote_gateway_req_id,
                        "error": {"code": -32000, "message": f"Internal error obtaining tools: {e!s}"},
                    }
        else:
            logger.warning(
                f"Received unhandled request method '{method}' (ID: {remote_gateway_req_id}) from gateway during handshake."
            )
            response_payload = {
                "jsonrpc": "2.0",
                "id": remote_gateway_req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }

        if response_payload and self.websocket and self.is_connected:
            logger.debug(
                f"Sending response for gateway request '{method}' (ID: {remote_gateway_req_id}): {str(response_payload)[:200]}..."
            )
            await self.send(response_payload)
        elif not self.websocket or not self.is_connected:
            logger.warning(f"Cannot send response for '{method}', websocket not available or not connected.")

    async def connect_and_run_listen_loop(  # noqa: C901
        self,
        proc_stdin: Optional[asyncio.StreamWriter],
        pending_stdio_requests: dict[Any, asyncio.Future],
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """Connects, performs handshake, and runs the listening loop."""
        if self._listen_task and not self._listen_task.done():
            logger.warning("connect_and_run_listen_loop called while a listen task is already active.")
            return self.is_connected

        # server_id para nosso pedido de registro
        # Se self.server_id não foi passado, GatewayClient gera um uuid4.
        # É crucial usar ESTE ID para rastrear a resposta do NOSSO 'register'.
        client_registration_id = self.server_id

        try:
            logger.info(f"Attempting to connect to gateway: {self.gateway_url}")
            async with websockets.connect(
                self.gateway_url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
                additional_headers=self.headers,
                ssl=self.ssl_context,
            ) as ws_connection:
                self.websocket = ws_connection  # Atribui aqui
                self.is_connected = True
                logger.info("Successfully connected to gateway.")

                registration_params = {"name": self.server_name, "version": "1.0.0", "capabilities": {}}
                registration_req = {
                    "jsonrpc": "2.0",
                    "id": client_registration_id,
                    "method": "register",
                    "params": registration_params,
                }
                logger.info(f"Sending client registration request (ID: {client_registration_id}): {registration_req}")
                await self.send(registration_req)  # Usa self.send que usa self.websocket

                # Handshake Loop: Espera por várias mensagens, incluindo respostas e pedidos do servidor
                # O servidor mcpport.gateway envia:
                # 1. Ack: {"status": "received", "id": client_registration_id, ...}
                # 2. Pedido: {"method": "initialize", "id": SERVER_INIT_ID, ...}
                # (Cliente responde ao initialize)
                # 3. Notificação: {"method": "notifications/initialized", ...}
                # (Servidor pode dormir 5s aqui)
                # 4. Pedido: {"method": "tools/list", "id": SERVER_TOOLS_ID, ...}
                # (Cliente responde ao tools/list)
                # 5. Confirmação final: {"status": "registered", "id": client_registration_id, ...}

                fully_registered_with_gateway = False
                # Timeout total para o handshake, e.g. 60 segundos
                handshake_timeout = 60.0
                loop_start_time = asyncio.get_running_loop().time()

                while not fully_registered_with_gateway:
                    remaining_time = handshake_timeout - (asyncio.get_running_loop().time() - loop_start_time)
                    if remaining_time <= 0:
                        logger.error("Handshake timed out waiting for 'status: registered' from gateway.")
                        return False

                    try:
                        if self.websocket is None:  # Checagem para mypy
                            logger.error("Websocket is None, cannot recv. Breaking handshake.")
                            return False  # Ou levante uma exceção
                        else:
                            # Usa um timeout menor para cada recv para permitir a verificação do timeout geral
                            response_raw = await asyncio.wait_for(
                                self.websocket.recv(), timeout=min(15.0, remaining_time)
                            )
                    except asyncio.TimeoutError:
                        # Isso significa que o recv individual deu timeout, não necessariamente o handshake todo.
                        # O loop externo verificará o timeout total do handshake.
                        logger.debug("Individual recv timed out, continuing handshake loop.")
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("Websocket connection closed during handshake.")
                        return False

                    logger.debug(f"Handshake recv: {str(response_raw)[:300]}...")
                    try:
                        msg_data = json.loads(response_raw)
                        msg_id = msg_data.get("id")

                        # Cenário 1: Resposta ao nosso pedido de registro
                        if msg_id == client_registration_id:
                            if msg_data.get("status") == "received":
                                logger.info(
                                    f"Gateway ACKed our registration (ID: {client_registration_id}). Continuing handshake."
                                )
                            elif msg_data.get("status") == "registered":
                                logger.info(
                                    f"Gateway confirmed full registration (ID: {client_registration_id}). Handshake successful."
                                )
                                fully_registered_with_gateway = True
                                # Não saia do loop ainda, pode haver mais mensagens antes do listen_loop
                            elif "result" in msg_data:  # Caso o servidor envie um JSON-RPC result para o register
                                logger.info(
                                    f"Gateway responded with 'result' for our registration (ID: {client_registration_id}). Assuming ACK."
                                )
                            elif "error" in msg_data:
                                logger.error(
                                    f"Gateway rejected our registration (ID: {client_registration_id}). Error: {msg_data['error']}"
                                )
                                return False
                            else:
                                logger.warning(
                                    f"Received unknown status/response for our registration ID {client_registration_id}: {msg_data}"
                                )

                        # Cenário 2: Um pedido do servidor para nós
                        elif "method" in msg_data and msg_id is not None:  # msg_id NÃO é client_registration_id
                            logger.info(f"Received request from gateway: method '{msg_data['method']}', ID '{msg_id}'.")
                            await self._handle_server_request_during_handshake(
                                msg_data,
                                proc_stdin,  # Passado aqui
                                pending_stdio_requests,  # Passado aqui
                                loop,  # Passado aqui
                            )

                        # Cenário 3: Uma notificação do servidor
                        elif "method" in msg_data and msg_id is None:
                            if msg_data["method"] == "notifications/initialized":
                                logger.info("Received 'notifications/initialized' from gateway.")
                            else:
                                logger.info(f"Received notification from gateway: {msg_data['method']}")

                        # Cenário 4: Um erro geral do servidor (não ligado a um ID nosso)
                        elif msg_data.get("status") == "error":
                            logger.error(
                                f"Handshake: Received general error from gateway: {msg_data.get('message', 'Unknown error')}"
                            )
                            if "Initialization failed" in msg_data.get(
                                "message", ""
                            ) or "Unsupported protocol version" in msg_data.get("message", ""):
                                logger.error("Server-side initialization or protocol handshake failed. Aborting.")
                                return False
                            # Adicione um caso para fechar se o erro for especificamente do nosso registro
                            if msg_data.get("message", "").startswith(
                                f"Registration processing error for ID {client_registration_id}"
                            ):
                                logger.error(
                                    f"Gateway reported error processing our registration {client_registration_id}. Aborting."
                                )
                                return False
                        else:
                            logger.debug(f"Received other message during handshake: {str(msg_data)[:100]}...")

                    except json.JSONDecodeError:
                        logger.error(f"Handshake: Invalid JSON received: {response_raw!r}")  # noqa: TRY400
                    except Exception as e:
                        logger.exception(f"Error processing message during handshake: {e}")  # noqa: TRY401
                        return False  # Erro inesperado no processamento

                if not fully_registered_with_gateway:  # Saiu do loop por timeout
                    logger.error(f"Handshake: Invalid JSON received: {response_raw!r}")
                    return False

                logger.info(
                    "Handshake sequence complete. Starting main message listening loop (_internal_listen_loop)."
                )
                await self._internal_listen_loop()  # Onde o cliente escuta por chamadas de ferramentas etc.
                return True  # Indica que o handshake e o início do listen loop foram OK

        except websockets.exceptions.InvalidStatus as e:
            logger.error(f"Gateway connection failed: HTTP {e.response.status_code}. Headers: {e.response.headers}")  # noqa: TRY400
        except ConnectionRefusedError:
            logger.error(f"Connection refused by gateway at {self.gateway_url}")  # noqa: TRY400
        except asyncio.TimeoutError:  # Timeout do websockets.connect ou do handshake_timeout geral
            logger.error(f"Timeout during connection or overall handshake with {self.gateway_url}")  # noqa: TRY400
        except asyncio.CancelledError:
            logger.info("Gateway connection and listen task was cancelled.")
        except Exception:
            logger.exception("Unhandled error during gateway connection or listening")
        finally:
            self.is_connected = False
            self.websocket = None  # <<< Garante que está None ao sair
            logger.debug("connect_and_run_listen_loop method finished.")
        return False

    async def close(self) -> None:
        logger.info("Closing GatewayClient...")
        self.is_connected = False

        # The _internal_listen_loop runs within connect_and_run_listen_loop's context.
        # If connect_and_run_listen_loop is a task, cancelling that task will
        # cause the 'async with websockets.connect(...)' to exit, closing the websocket.
        # Direct cancellation of _listen_task is not needed if it's not directly managed.
        # However, if connect_and_run_listen_loop is itself a task, that's what needs cancelling.

        # if self.websocket and not self.websocket.closed:
        if self.websocket:
            logger.debug("Explicitly closing websocket in GatewayClient.close().")
            try:
                await self.websocket.close(code=1000, reason="Client shutdown initiated")
            except Exception:  # pylint: disable=broad-except
                logger.exception("Exception during websocket close in GatewayClient.close")

        self.websocket = None
        self.message_handlers.clear()
        logger.info("GatewayClient resources released.")


async def _actual_cleanup(  # noqa: C901
    shutdown_event: asyncio.Event,
    gateway_client: Optional[GatewayClient],
    proc: Optional[asyncio.subprocess.Process],
    background_tasks: list[asyncio.Task],
    main_operational_tasks: list[asyncio.Task],
) -> None:
    """The actual asynchronous cleanup logic. Designed to be idempotent."""
    async with _cleanup_lock:
        if _cleanup_started_event.is_set():
            logger.debug("Cleanup already performed or in progress.")
            return
        _cleanup_started_event.set()

    logger.info("Commencing actual cleanup operations...")
    shutdown_event.set()  # Signal all dependent tasks to stop

    # 1. Cancel main operational tasks (gateway connection, subprocess monitor)
    logger.debug("Cancelling main operational tasks...")
    for task in main_operational_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*main_operational_tasks, return_exceptions=True)  # Wait for them to finish/cancel
    logger.info("Main operational tasks processed.")

    # 2. Close gateway client (which should handle its own websocket closing)
    if gateway_client:
        logger.debug("Closing gateway client...")
        await gateway_client.close()
        logger.info("Gateway client closed.")

    # 3. Terminate subprocess
    if proc:  # Check if proc was initialized
        if proc.returncode is None:  # Process is still running or status unknown
            logger.info(f"Attempting to terminate subprocess (PID: {proc.pid})...")

            # Explicitly close stdin if it's open, to signal EOF to the subprocess
            # and help release its transport.
            if proc.stdin and hasattr(proc.stdin, "close") and not proc.stdin.is_closing():
                logger.debug(f"Closing stdin for subprocess {proc.pid}.")
                try:
                    proc.stdin.close()
                    # await proc.stdin.wait_closed() # wait_closed can hang if not drained properly.
                    # Closing is usually enough.
                except Exception as e_stdin:  # pylint: disable=broad-except
                    logger.warning(f"Error closing stdin for subprocess {proc.pid}: {e_stdin}")

            logger.debug(f"Sending SIGTERM to subprocess {proc.pid}.")
            proc.terminate()  # Sends SIGTERM
            try:
                # Wait for the process to terminate
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                logger.info(f"Subprocess {proc.pid} terminated with code {proc.returncode}.")
            except asyncio.TimeoutError:
                logger.warning(
                    f"Subprocess {proc.pid} did not terminate with SIGTERM within timeout. Sending SIGKILL..."
                )
                proc.kill()  # Sends SIGKILL
                await proc.wait()  # Wait for kill to complete
                logger.info(f"Subprocess {proc.pid} killed, exit code {proc.returncode}.")
            except Exception as e_term:  # pylint: disable=broad-except
                logger.exception(f"Error during subprocess {proc.pid} termination: {e_term}")  # noqa: TRY401
        else:
            logger.info(f"Subprocess {proc.pid} already exited (code {proc.returncode}).")

        # Give a very brief moment for any final I/O events related to pipes to be processed
        # This might help transports to be properly closed and GC'd before loop exits.
        # try:
        #     await asyncio.sleep(0.05)  # Increased slightly
        # except asyncio.CancelledError:
        #     pass  # If cleanup itself is cancelled
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0.05)

    else:
        logger.debug("No subprocess instance to terminate in cleanup.")
    # 4. Cancel and gather remaining background I/O tasks
    logger.debug("Cancelling background I/O tasks...")
    for task in background_tasks:
        if not task.done():
            task.cancel()

    results = await asyncio.gather(*background_tasks, return_exceptions=True)
    for i, result in enumerate(results):
        task_name = background_tasks[i].get_name()
        if isinstance(result, asyncio.CancelledError):
            logger.debug(f"I/O Task '{task_name}' was cancelled.")
        elif isinstance(result, Exception):
            logger.error(f"I/O Task '{task_name}' raised an error during cleanup: {result!r}")
    logger.info("Background I/O tasks processed.")

    logger.info("Actual cleanup operations finished.")


async def stdio_to_ws(args: StdioToWsArgs) -> None:  # noqa: C901
    pending_stdio_requests: dict[Any, asyncio.Future] = {}
    loop = asyncio.get_running_loop()  # Garante que loop está definido

    # Task for stdio_to_ws itself, useful for targeted cancellation by signal handler
    stdio_to_ws_task = asyncio.current_task()
    if stdio_to_ws_task:
        stdio_to_ws_task.set_name("stdio_to_ws_orchestrator")

    # Initialize resources that need cleanup
    gateway_client: Optional[GatewayClient] = None
    proc: Optional[asyncio.subprocess.Process] = None
    background_io_tasks: list[asyncio.Task] = []
    main_operational_tasks: list[asyncio.Task] = []  # For gateway connection and proc monitor

    shutdown_event = asyncio.Event()  # Used to signal tasks to wind down

    # --- Signal Handling Setup ---
    def signal_handler_sync_callback(sig: signal.Signals) -> None:
        logger.info(f"Received OS signal: {sig.name}. Initiating graceful shutdown.")
        # Schedule the async cleanup to run in the loop. This call is non-blocking.
        asyncio.create_task(  # noqa: RUF006
            _actual_cleanup(shutdown_event, gateway_client, proc, background_io_tasks, main_operational_tasks),
            name="signal_triggered_cleanup_task",
        )

        # Also, attempt to cancel the main orchestrator task to break its main loop
        if stdio_to_ws_task and not stdio_to_ws_task.done():
            if stdio_to_ws_task.cancel():
                logger.debug(f"Cancellation request sent to '{stdio_to_ws_task.get_name()}' due to signal.")
            else:  # Should not happen if task is running
                logger.warning(f"Could not send cancellation request to '{stdio_to_ws_task.get_name()}'.")

    for sig_val in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig_val, signal_handler_sync_callback, sig_val)
        except RuntimeError:  # e.g., on Windows if loop is not ProactorEventLoop
            logger.warning(
                f"Could not add asyncio signal handler for {sig_val.name}. OS signal handling may be limited."
            )
    # --- End Signal Handling Setup ---

    try:
        # --- Configuration and Client Initialization ---
        stdio_cmd = args.stdio_cmd
        auth_headers = {}
        if args.gateway_auth_token:
            auth_headers["Authorization"] = args.gateway_auth_token
        if isinstance(args.headers, dict):
            auth_headers.update(args.headers)
        elif isinstance(args.headers, list):
            for item in args.headers:
                if ":" in item:
                    key, value = item.split(":", 1)
                    auth_headers[key.strip()] = value.strip()

        ssl_context_to_use = None
        if not args.ssl_verify:
            ssl_context_to_use = ssl.create_default_context()
            ssl_context_to_use.check_hostname = False
            ssl_context_to_use.verify_mode = ssl.CERT_NONE
            logger.info("SSL certificate verification disabled.")
        elif args.ssl_ca_cert:
            ssl_context_to_use = ssl.create_default_context(cafile=args.ssl_ca_cert)
            logger.info(f"Using custom CA certificate: {args.ssl_ca_cert}")
        elif args.gateway_url.startswith("wss://"):
            ssl_context_to_use = ssl.create_default_context()
            logger.info("Using default SSL context for wss connection.")

        logger.info(f"Starting MCP Gateway Client: Name='{args.server_name}', Gateway='{args.gateway_url}'")
        gateway_client = GatewayClient(
            gateway_url=args.gateway_url,
            server_name=args.server_name,
            server_id=args.server_id,
            headers=auth_headers,
            ssl_context=ssl_context_to_use,
        )
        child_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)  # Maxsize to prevent runaway memory usage
        # --- End Configuration ---

        # --- Start Subprocess ---
        logger.info(f"Starting subprocess with command: {stdio_cmd}")
        proc = await asyncio.create_subprocess_shell(  # noqa: S604
            stdio_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True,  # Ensure shell=True is intended and secure
        )
        logger.info(f"Subprocess started (PID: {proc.pid}).")
        # --- End Subprocess ---

        # --- Define and Start I/O Tasks ---
        # Se for aninhada, pode usar vars do escopo
        async def read_stdout_to_queue() -> None:  # noqa: C901
            logger.debug("read_stdout_to_queue task started.")
            try:
                # Estas vars (proc, pending_stdio_requests, child_queue, shutdown_event)
                # devem vir do escopo de stdio_to_ws
                while proc and proc.stdout and not proc.stdout.at_eof() and not shutdown_event.is_set():
                    line_bytes = await proc.stdout.readline()
                    if not line_bytes:
                        logger.info("read_stdout_to_queue: EOF reached.")
                        break
                    line_str = line_bytes.decode("utf-8", errors="replace").strip()
                    if line_str:
                        try:
                            json_msg = json.loads(line_str)
                            msg_id = json_msg.get("id")

                            if msg_id is not None and msg_id in pending_stdio_requests:
                                future = pending_stdio_requests.pop(msg_id)
                                if not future.done():
                                    if "result" in json_msg:
                                        future.set_result(json_msg["result"])
                                    elif "error" in json_msg:
                                        future.set_exception(
                                            RuntimeError(f"STDIO server error for ID {msg_id}: {json_msg['error']}")
                                        )
                                    else:
                                        future.set_exception(
                                            RuntimeError(f"Invalid response from STDIO for ID {msg_id}")
                                        )
                                logger.debug(f"STDIO Response for internal client req ID {msg_id} processed.")
                            else:
                                logger.debug(
                                    f"STDIO (unsolicited/notification) -> Gateway (via child_queue): {line_str[:100]}..."
                                )
                                await child_queue.put(json_msg)
                        except json.JSONDecodeError:
                            logger.warning(f"STDIO Non-JSON output: {line_str[:100]}...")
                        except Exception as e_proc_line:
                            logger.exception(f"Error processing line from stdio: {e_proc_line}")  # noqa: TRY401
            except asyncio.CancelledError:
                logger.info("read_stdout_to_queue task cancelled.")
            except Exception as e_outer:
                logger.exception(f"Outer error in read_stdout_to_queue: {e_outer}")  # noqa: TRY401
            finally:
                logger.debug("read_stdout_to_queue task finished.")

        async def read_stderr_log() -> None:
            logger.debug("read_stderr_log task started.")
            try:
                while proc and proc.stderr and not proc.stderr.at_eof() and not shutdown_event.is_set():
                    line_bytes = await proc.stderr.readline()
                    if not line_bytes:
                        break
                    line_str = line_bytes.decode("utf-8", errors="replace").strip()
                    if line_str:
                        logger.info(f"Subprocess STDERR: {line_str}")
            except asyncio.CancelledError:
                logger.info("read_stderr_log task cancelled.")
            except Exception:
                logger.exception("Error in read_stderr_log")  # pylint: disable=broad-except
            finally:
                logger.debug("read_stderr_log task finished.")

        async def forward_queue_to_gateway() -> None:
            logger.debug("forward_queue_to_gateway task started.")
            try:
                while not shutdown_event.is_set():
                    try:
                        json_msg = await asyncio.wait_for(child_queue.get(), timeout=0.5)  # Periodically check shutdown
                        if gateway_client and gateway_client.is_connected:
                            await gateway_client.send(json_msg)
                        else:
                            logger.debug("Gateway not connected; message from queue discarded.")
                        child_queue.task_done()
                    except asyncio.TimeoutError:
                        continue
            except asyncio.CancelledError:
                logger.info("forward_queue_to_gateway task cancelled.")
            except Exception:
                logger.exception("Error in forward_queue_to_gateway")  # pylint: disable=broad-except
            finally:
                logger.debug("forward_queue_to_gateway task finished.")

        async def forward_gateway_to_stdio(message: Any, _: Optional[str]) -> None:
            if proc and proc.stdin and not proc.stdin.is_closing() and not shutdown_event.is_set():
                message_str = json.dumps(message) + "\n"
                logger.debug(f"Gateway → STDIO: {message_str[:100]}...")
                try:
                    proc.stdin.write(message_str.encode("utf-8"))
                    await proc.stdin.drain()
                except (ConnectionResetError, BrokenPipeError):
                    logger.warning("Subprocess stdin closed or broken pipe.")
                except asyncio.CancelledError:
                    raise  # Propagate if cancelled during write
                except Exception:
                    logger.exception("Error writing to subprocess stdin")  # pylint: disable=broad-except
            elif shutdown_event.is_set():
                logger.debug("Shutdown active; not forwarding to STDIO.")

        gateway_client.add_message_handler("forward_to_stdio", forward_gateway_to_stdio)

        io_tasks_map = {
            "read_stdout": read_stdout_to_queue,
            "read_stderr": read_stderr_log,
            "forward_queue": forward_queue_to_gateway,
        }
        for name, coro_func in io_tasks_map.items():
            task = asyncio.create_task(coro_func(), name=name)
            background_io_tasks.append(task)
        # --- End I/O Tasks ---

        # --- Main Operational Loop ---
        # Task for gateway connection and its internal listen loop
        # gateway_connect_listen_task = asyncio.create_task(
        #     gateway_client.connect_and_run_listen_loop(), name="gateway_connect_listen_task"
        # )
        gateway_connect_listen_task = asyncio.create_task(
            gateway_client.connect_and_run_listen_loop(proc.stdin if proc else None, pending_stdio_requests, loop),
            name="gateway_connect_listen_task",
        )
        main_operational_tasks.append(gateway_connect_listen_task)

        # Task for monitoring subprocess exit
        proc_wait_task = asyncio.create_task(proc.wait(), name="subprocess_wait_task")
        main_operational_tasks.append(proc_wait_task)

        # Wait for any main operational task to complete (or shutdown event)
        # The shutdown_event itself is handled by the signal handler cancelling stdio_to_ws_task
        # or by _actual_cleanup being called directly.

        # This loop will break if stdio_to_ws_task is cancelled (e.g., by signal)
        # or if one of the main operational tasks finishes.
        done, pending = await asyncio.wait(main_operational_tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            task_name = task.get_name()
            if task.exception():
                logger.error(f"Main operational task '{task_name}' failed: {task.exception()!r}")
            else:
                logger.info(f"Main operational task '{task_name}' completed with result: {task.result()!r}")
                if task is proc_wait_task:
                    logger.info(f"Subprocess exited with code: {proc.returncode}")
                elif task is gateway_connect_listen_task:
                    logger.info("Gateway connection/listen task finished.")

        logger.info("A main operational task has finished. Initiating shutdown sequence if not already started.")
        # Trigger cleanup explicitly if we didn't get here via signal (cancellation)
        if not _cleanup_started_event.is_set():
            await _actual_cleanup(shutdown_event, gateway_client, proc, background_io_tasks, main_operational_tasks)
        else:  # If cleanup was started by signal, stdio_to_ws_task would be cancelled.
            # We might be here if a main task finished *before* a signal arrived.
            # The cleanup already ensures it runs once. Awaiting it here makes sure it completes.
            logger.debug("Cleanup was already initiated, awaiting its completion if necessary.")
            # Find the cleanup task if created by signal handler and await it, or re-trigger if needed.
            # This part is tricky. Better to rely on the finally block if stdio_to_ws_task isn't cancelled.
            # The finally block will call _actual_cleanup.

        # --- End Main Operational Loop ---

    except asyncio.CancelledError:
        logger.info(
            f"Orchestrator task '{stdio_to_ws_task.get_name() if stdio_to_ws_task else 'stdio_to_ws'}' was cancelled. "
            "Cleanup will be handled by the signal handler or finally block."
        )
        # If cancelled, the finally block will still run.
        # The signal handler already schedules _actual_cleanup.
    except Exception:  # pylint: disable=broad-except
        logger.exception("Fatal error in stdio_to_ws orchestrator")
    finally:
        logger.info("stdio_to_ws orchestrator entering 'finally' block. Ensuring cleanup...")
        # This is the ultimate safety net for cleanup.
        # _actual_cleanup is idempotent so it's safe to call here.
        await _actual_cleanup(shutdown_event, gateway_client, proc, background_io_tasks, main_operational_tasks)

        # Remove signal handlers after cleanup
        # for sig_val in (signal.SIGINT, signal.SIGTERM):
        #     try:
        #         loop.remove_signal_handler(sig_val)
        #     except RuntimeError:
        #         pass  # May fail if loop is closed or handler not set
        for sig_val in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(RuntimeError):
                loop.remove_signal_handler(sig_val)

        _cleanup_started_event.clear()  # Reset for potential re-runs in tests
        logger.info("stdio_to_ws orchestrator fully exited.")


def main(args: StdioToWsArgs) -> None:
    # Reset global state for idempotency if main is called multiple times (e.g. tests)
    _cleanup_started_event.clear()

    try:
        asyncio.run(stdio_to_ws(args))
    except KeyboardInterrupt:
        # This typically happens if Ctrl+C is pressed *very* early,
        # before asyncio.run fully sets up its own signal handling,
        # or if our signal handler doesn't exit and cancellation propagates.
        logger.info("KeyboardInterrupt received at top level. Program is exiting.")
    except SystemExit as e:
        # This can happen if sys.exit() is called somewhere unexpected,
        # or if our signal handler's sys.exit() propagates up.
        logger.info(f"SystemExit({e.code}) caught at top level. Program is exiting.")
    except Exception:  # pylint: disable=broad-except
        logger.exception("Unhandled exception in mcpgateway_client.client_websocket.main top level")
        sys.exit(1)  # Ensure non-zero exit for unhandled errors
