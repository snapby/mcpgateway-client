from typing import Optional


class StdioToWsArgs:
    """Data class for command line arguments"""

    def __init__(
        self,
        gateway_url: str,
        stdio_cmd: str,
        gateway_auth_token: Optional[str] = None,
        port: int = 0,  # 0 means don't start local WebSocket server
        enable_cors: bool = False,
        health_endpoints: Optional[list[str]] = None,
        server_name: str = "mcp-stdio-gateway",
        server_id: Optional[str] = None,
        require_gateway: bool = True,
        headers: Optional[dict] = None,
        ssl_verify: bool = True,  # Whether to verify SSL certificates
        ssl_ca_cert: Optional[str] = None,  # Custom CA certificate file path
        log_level: str = "INFO",
    ):
        self.stdio_cmd = stdio_cmd
        self.port = port
        self.enable_cors = enable_cors
        self.health_endpoints = health_endpoints or []
        self.gateway_url = gateway_url
        self.gateway_auth_token = gateway_auth_token
        self.server_name = server_name
        self.server_id = server_id
        self.require_gateway = require_gateway
        self.headers = headers or {}
        self.ssl_verify = ssl_verify
        self.ssl_ca_cert = ssl_ca_cert
        self.log_level = log_level
