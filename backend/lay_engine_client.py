"""
HTTP client for the CHIMERA Lay Engine Data API.
Server-to-server calls — not affected by CORS.
"""

import httpx
import logging
from typing import Optional

log = logging.getLogger("lay_engine_client")


class LayEngineClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-API-Key": api_key}

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{self.base_url}{path}",
                headers=self.headers,
                params=params or {},
            )
            r.raise_for_status()
            return r.json()

    async def get_sessions(
        self, date: Optional[str] = None, mode: Optional[str] = None
    ) -> dict:
        """GET /api/data/sessions — list sessions, filter by date/mode."""
        params = {}
        if date:
            params["date"] = date
        if mode:
            params["mode"] = mode
        return await self._get("/api/data/sessions", params)

    async def get_session_detail(self, session_id: str) -> dict:
        """GET /api/data/sessions/{session_id} — single session detail."""
        return await self._get(f"/api/data/sessions/{session_id}")

    async def get_bets(
        self, date: Optional[str] = None, mode: Optional[str] = None
    ) -> dict:
        """GET /api/data/bets — all bets, filter by date/mode."""
        params = {}
        if date:
            params["date"] = date
        if mode:
            params["mode"] = mode
        return await self._get("/api/data/bets", params)

    async def get_results(self, date: Optional[str] = None) -> dict:
        """GET /api/data/results — all rule evaluations, filter by date."""
        params = {}
        if date:
            params["date"] = date
        return await self._get("/api/data/results", params)

    async def get_state(self) -> dict:
        """GET /api/data/state — live engine state."""
        return await self._get("/api/data/state")

    async def get_rules(self) -> dict:
        """GET /api/data/rules — active rule definitions."""
        return await self._get("/api/data/rules")

    async def get_summary(self, date: Optional[str] = None) -> dict:
        """GET /api/data/summary — aggregated statistics."""
        params = {}
        if date:
            params["date"] = date
        return await self._get("/api/data/summary", params)
