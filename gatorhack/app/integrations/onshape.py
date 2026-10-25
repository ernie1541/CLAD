"""Optional Onshape hand-off: push the released STEP assembly into a new Onshape document.

Uses Onshape API keys (Basic auth). Create keys at https://dev-portal.onshape.com → API keys,
then paste them on the Dev page.
"""
from __future__ import annotations

from pathlib import Path

import httpx


def _client(cfg: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg["onshape_base_url"].rstrip("/"),
        auth=(cfg["onshape_access_key"], cfg["onshape_secret_key"]),
        headers={"Accept": "application/json;charset=UTF-8; qs=0.09"},
        timeout=60,
    )


def configured(cfg: dict) -> bool:
    return bool(cfg.get("onshape_access_key") and cfg.get("onshape_secret_key"))


async def test(cfg: dict) -> dict:
    if not configured(cfg):
        return {"ok": False, "detail": "Add Onshape API keys on this page first."}
    async with _client(cfg) as c:
        r = await c.get("/api/v6/users/sessioninfo")
    if r.status_code == 200:
        j = r.json()
        return {"ok": True, "detail": f"Signed in as {j.get('name') or j.get('email') or 'Onshape user'}"}
    return {"ok": False, "detail": f"HTTP {r.status_code}: {r.text[:200]}"}


async def upload_step(cfg: dict, name: str, step_path: Path) -> dict:
    if not configured(cfg):
        raise RuntimeError("Onshape API keys are not set (Dev page).")
    async with _client(cfg) as c:
        r = await c.post("/api/v6/documents", json={"name": name, "isPublic": False})
        r.raise_for_status()
        doc = r.json()
        did, wid = doc["id"], doc["defaultWorkspace"]["id"]
        with open(step_path, "rb") as fh:
            files = {"file": (step_path.name, fh, "application/octet-stream")}
            data = {"encodedFilename": step_path.name, "translate": "true", "flattenAssemblies": "false",
                    "createComposite": "false", "storeInDocument": "true"}
            r = await c.post(f"/api/v6/blobelements/d/{did}/w/{wid}", files=files, data=data)
        r.raise_for_status()
    base = cfg["onshape_base_url"].rstrip("/")
    return {"ok": True, "document_id": did, "url": f"{base}/documents/{did}/w/{wid}"}
