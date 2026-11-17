"""PandaData MCP 客户端（从 skill-futures-session-split 抽取，令牌逻辑保持一致）。

令牌轮换是这类取数最大的事故源：refresh_token 单次有效，多进程并发续期会互相作废。
这里保留三道防线：续期前先读磁盘缓存、续期被拒回退磁盘令牌、平台清空 tokens 时容错。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OAUTH_PATH = Path.home() / ".dsh-quantstudio" / "quantskills" / "panda-mcp" / "oauth.json"
MCP_URL = "https://pandadatamcp.pandaaiquant.com/mcp"
PREVIEW_ROWS = 2000

TOKEN_CACHE = Path.home() / ".dsh-logs" / "panda_token.json"
TOKEN_ENDPOINT = "https://pandadatamcp.pandaaiquant.com/token"


class McpClient:
    """令牌来自平台 OAuth；过期时用 refresh_token 自动续期，续期结果写入本地缓存。"""

    def __init__(self) -> None:
        oauth = json.loads(OAUTH_PATH.read_text())
        self.client_id = oauth["client"]["client_id"]
        tokens = oauth.get("tokens") or {}
        if TOKEN_CACHE.exists():
            cached = json.loads(TOKEN_CACHE.read_text())
            tokens = {**tokens, **cached}
        self.token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token")

    def _adopt_cache(self) -> bool:
        """采纳磁盘上更新的令牌（其他进程可能已续期，refresh_token 会轮换）。"""

        if not TOKEN_CACHE.exists():
            return False
        tokens = json.loads(TOKEN_CACHE.read_text())
        if tokens.get("access_token") == self.token:
            return False
        self.token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        return True

    def refresh(self) -> None:
        if self._adopt_cache():
            print("  复用其他进程已续期的令牌")
            return
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
        }).encode()
        req = urllib.request.Request(TOKEN_ENDPOINT, data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                tokens = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if self._adopt_cache():
                print("  续期被拒，但磁盘令牌可用，改为复用")
                return
            raise RuntimeError(
                "令牌续期失败（refresh_token 可能已被其他进程消费）："
                f"{exc.code} {exc.read().decode('utf-8', 'ignore')[:120]}"
            ) from exc
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps(tokens, ensure_ascii=False, indent=1))
        self.token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        print("  令牌已续期")

    def _post(self, body: dict) -> dict:
        for attempt in range(2):
            req = urllib.request.Request(
                MCP_URL,
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    raw = resp.read().decode("utf-8", "ignore")
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self.refresh()
                    continue
                raise
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {"raw": raw}

    def init(self) -> None:
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "futures-session-split", "version": "1.0"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, method: str, params: dict, preview_rows: int | None = None) -> dict:
        arguments = {"method": method, "params": params}
        if preview_rows:
            arguments["preview_rows"] = preview_rows
        res = self._post({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                          "params": {"name": "call_pandadata", "arguments": arguments}})
        if "result" not in res:
            raise RuntimeError(f"MCP 异常：{json.dumps(res, ensure_ascii=False)[:300]}")
        return res["result"]["structuredContent"]


