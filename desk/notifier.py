"""Telegram notifier. URL on every button comes from links.py, passed in unchanged."""
import asyncio, json, logging, os, time
import aiohttp

log = logging.getLogger("notifier")


class Telegram:
    def __init__(self, token, chat_id, db=None):
        self._token = token
        self.chat_id = chat_id
        self.db = db
        self.session = None
        self.offset = None

    @property
    def _base(self):
        return f"https://api.telegram.org/bot{self._token}/"

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))

    async def close(self):
        if self.session:
            await self.session.close()

    async def _call(self, method, payload, timeout=20):
        async with self.session.post(self._base + method, json=payload,
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            j = await r.json(content_type=None)
        if not j.get("ok"):
            raise RuntimeError(f"telegram {method}: {j.get('error_code')} {j.get('description')}")
        return j["result"]

    async def push(self, text, url_buttons=(), callbacks=(), mint=None, retries=3):
        """url_buttons: [(label, url_from_links_py)], callbacks: [(label, data)]. 3 tries.
        Returns message_id or None (caller decides what a failure means)."""
        rows = []
        if url_buttons:
            rows.append([{"text": l, "url": u} for l, u in url_buttons])
        if callbacks:
            rows.append([{"text": l, "callback_data": d} for l, d in callbacks])
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if rows:
            payload["reply_markup"] = {"inline_keyboard": rows}
        for i in range(retries):
            try:
                res = await self._call("sendMessage", payload)
                if self.db:
                    self.db.x("INSERT INTO pushes(ts,text,mint,ok) VALUES(?,?,?,1)", time.time(), text, mint)
                return res["message_id"]
            except Exception as e:  # noqa
                log.warning("push try %d failed: %s", i + 1, str(e).replace(self._token, "***"))
                await asyncio.sleep(1 + i)
        if self.db:
            self.db.x("INSERT INTO pushes(ts,text,mint,ok) VALUES(?,?,?,0)", time.time(), text, mint)
        return None

    async def reply(self, text):
        return await self.push(text, retries=2)

    async def answer_callback(self, cb_id, text=""):
        try:
            await self._call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})
        except Exception:
            pass

    async def updates(self):
        """long-poll; yields (kind, payload) only for the owner chat"""
        params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if self.offset is not None:
            params["offset"] = self.offset
        res = await self._call("getUpdates", params, timeout=35)
        out = []
        for u in res:
            self.offset = u["update_id"] + 1
            if "message" in u:
                m = u["message"]
                if m.get("chat", {}).get("id") == self.chat_id and "text" in m:
                    out.append(("text", m["text"]))
            elif "callback_query" in u:
                c = u["callback_query"]
                if c.get("from", {}).get("id") == self.chat_id or \
                        c.get("message", {}).get("chat", {}).get("id") == self.chat_id:
                    out.append(("callback", {"id": c["id"], "data": c.get("data", "")}))
        return out
