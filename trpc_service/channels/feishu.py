"""Self-built Feishu app: authenticated WS ingress and durable plain-text replies.

The official SDK owns WS framing/ACKs. Its public blocking start() runs in an
owned process; no SDK internals or global event loops are patched.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import multiprocessing
import re
import time
import uuid

import httpx

from .base import CallbackVerificationError
from .events import TransportEvent, DeliveryResult, action_command
from .models import NormalizedInboundMessage

API = "https://open.feishu.cn/open-apis"
TEXT_BYTES = 3000


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise CallbackVerificationError("invalid Feishu identity")
    return value


class LifecycleLogHandler(logging.Handler):
    """Export only lifecycle markers, never signed URLs, tokens or payloads."""

    def __init__(self, connection):
        super().__init__()
        self.connection = connection

    def emit(self, record):
        if record.funcName == "_connect" and "connected to " in str(record.msg):
            self.connection.send({"kind": "connected"})
        elif record.funcName == "_receive_message_loop" and "loop exit" in str(record.msg):
            self.connection.send({"kind": "failed"})
        elif record.funcName == "_handle_data_frame" and "receive message," in str(record.msg):
            self.connection.send({"kind": "protocol_frame"})
        elif record.funcName == "_handle_control_frame" and "receive pong" in str(record.msg):
            self.connection.send({"kind": "heartbeat"})


def commit_callback(connection, payload, timeout=2):
    """Return to the SDK (which ACKs) only after the parent commits ingress."""
    nonce = uuid.uuid4().hex
    connection.send({"kind": "event", "nonce": nonce, "payload": payload})
    if not connection.poll(timeout):
        connection.send({"kind": "failed"})
        raise RuntimeError("ingress acknowledgement timeout")
    ack = connection.recv()
    if ack != {"nonce": nonce, "committed": True}:
        connection.send({"kind": "failed"})
        raise RuntimeError("ingress not committed")


def websocket_process(connection, app_id, app_secret):
    try:
        import lark_oapi as lark
        logger = logging.getLogger("Lark")
        logger.handlers.clear()
        logger.propagate = False
        logger.addHandler(LifecycleLogHandler(connection))

        def receive(event):
            # Select known protocol fields; header.token never crosses IPC.
            frame = json.loads(lark.JSON.marshal(event))
            header = frame.get("header") or {}
            frame["header"] = {key: header.get(key) for key in ("app_id", "tenant_key", "event_type", "event_id")}
            commit_callback(connection, frame)

        handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(receive).build()
        client = lark.ws.Client(app_id,
                                app_secret,
                                event_handler=handler,
                                log_level=lark.LogLevel.DEBUG,
                                auto_reconnect=False)
        client.start()
    except BaseException:
        try:
            connection.send({"kind": "failed"})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class FeishuAdapter:

    def __init__(self, app_id, app_secret, *, transport=None):
        self.app_id, self._secret = identifier(app_id), app_secret
        self.http = httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport)
        self._token, self._expires = None, 0
        self._auth_lock = asyncio.Lock()
        self.bot_open_id = None
        self._process = self._connection = None
        self.diagnostics = {
            "connections": 0,
            "heartbeats": 0,
            "protocol_frames": 0,
            "message_events": 0,
            "chat_events": 0,
            "ignored_events": 0
        }

    async def token(self):
        async with self._auth_lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            try:
                response = await self.http.post(API + "/auth/v3/tenant_access_token/internal",
                                                json={
                                                    "app_id": self.app_id,
                                                    "app_secret": self._secret
                                                })
                data = response.json()
                token, expire = data.get("tenant_access_token"), data.get("expire")
                if (response.status_code != 200 or data.get("code") != 0 or not isinstance(token, str) or not token
                        or type(expire) is not int or expire <= 60):
                    raise ValueError()
                self._token, self._expires = token, time.monotonic() + expire - 60
                return token
            except Exception:
                raise CallbackVerificationError("Feishu authentication failed") from None

    async def initialize(self):
        try:
            response = await self.http.get(API + "/bot/v3/info",
                                           headers={"Authorization": "Bearer " + await self.token()})
            data = response.json()
            bot = data.get("bot") or {}
            # Some official bot-info responses omit app_id; the internal token
            # was already acquired with this app's ID + secret.
            if response.status_code != 200 or data.get("code") != 0 or bot.get("app_id", self.app_id) != self.app_id:
                raise ValueError()
            self.bot_open_id = identifier(bot.get("open_id"))
        except Exception:
            raise CallbackVerificationError("Feishu bot identity could not be verified") from None

    def normalize(self, frame, binding):
        # Only receive() may feed this boundary in a running service. HTTP
        # callbacks are intentionally not exposed without signature validation.
        header = frame.get("header") or {}
        if (binding.channel.value != "feishu" or binding.external_account_id != self.app_id
                or header.get("app_id") != self.app_id):
            raise CallbackVerificationError("Feishu account mismatch")
        tenant_key = identifier(header.get("tenant_key"))
        body = frame.get("event") or {}
        sender, raw = body.get("sender") or {}, body.get("message") or {}
        event_id = identifier(raw.get("message_id") or header.get("event_id"))

        def ignored(reason):
            return TransportEvent(event_id=event_id, kind="ignored", reason=reason)

        if header.get("event_type") != "im.message.receive_v1":
            return ignored("unsupported_event")
        if sender.get("sender_type") != "user":
            return ignored("non_user_sender")
        if sender.get("tenant_key") != tenant_key:
            raise CallbackVerificationError("Feishu enterprise mismatch")
        actor = tenant_key + ":" + identifier((sender.get("sender_id") or {}).get("open_id"))
        chat_id = identifier(raw.get("chat_id"))
        chat = tenant_key + ":" + chat_id
        if raw.get("message_type") != "text":
            return ignored("unsupported_media")
        if raw.get("chat_type") not in {"p2p", "group"}:
            return ignored("unsupported_conversation")
        try:
            text = json.loads(raw.get("content", "")).get("text")
        except (ValueError, AttributeError, TypeError):
            return ignored("invalid_text")
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            return ignored("invalid_text")
        group = raw["chat_type"] == "group"
        if group:
            mentions = [
                item for item in raw.get("mentions") or []
                if self.bot_open_id and (item.get("id") or {}).get("open_id") == self.bot_open_id
                and isinstance(item.get("key"), str) and item["key"] and item["key"] in text
            ]
            if not mentions:
                return ignored("bot_not_mentioned")
            for mention in mentions:
                text = text.replace(mention["key"], "")
            text = text.strip()
            if not text:
                return ignored("empty_mention")
        native_thread = raw.get("thread_id") if group else None
        reply_root = (raw.get("root_id") or raw.get("parent_id")) if group else None
        thread = native_thread or reply_root
        if thread:
            thread = identifier(thread)
        reply = {
            "app_id": self.app_id,
            "tenant_key": tenant_key,
            "chat_id": chat_id,
            "message_id": identifier(raw.get("message_id")),
            "thread_id": thread,
            "reply_in_thread": bool(native_thread)
        }
        message = NormalizedInboundMessage(channel="feishu",
                                           webhook_public_id=binding.webhook_public_id,
                                           external_message_id=event_id,
                                           external_user_id=actor,
                                           external_chat_id=chat,
                                           conversation_type="thread" if thread else "group" if group else "direct",
                                           thread_id=thread,
                                           text=text,
                                           received_at=datetime.now(timezone.utc),
                                           request_id=event_id)
        return action_command(message, reply) or TransportEvent(event_id=event_id,
                                                                kind="chat",
                                                                message=message,
                                                                external_actor_id=actor,
                                                                external_chat_id=chat,
                                                                reply_context=reply)

    async def receive(self, binding, callback, ready=None, *, on_ready=None):
        if self._process is not None:
            raise RuntimeError("Feishu connection already owned")
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(target=websocket_process, args=(child, self.app_id, self._secret))
        self._process.start()
        child.close()
        connected, deadline = False, time.monotonic() + 30
        try:
            while True:
                if self._connection.poll():
                    item = self._connection.recv()
                    if item["kind"] == "connected":
                        self.diagnostics["connections"] += 1
                        connected = True
                        if ready:
                            ready.set()
                        if on_ready:
                            on_ready()
                    elif item["kind"] == "event":
                        self.diagnostics["message_events"] += 1
                        try:
                            if not connected:
                                raise CallbackVerificationError("Feishu connection not authenticated")
                            event = self.normalize(item["payload"], binding)
                            if event.kind in {"chat", "ignored"}:
                                self.diagnostics[event.kind + "_events"] += 1
                            await asyncio.wait_for(callback(event), 1.5)
                        except BaseException:
                            self._connection.send({"nonce": item["nonce"], "committed": False})
                            raise
                        self._connection.send({"nonce": item["nonce"], "committed": True})
                    elif item["kind"] == "protocol_frame":
                        self.diagnostics["protocol_frames"] += 1
                    elif item["kind"] == "heartbeat":
                        self.diagnostics["heartbeats"] += 1
                    else:
                        raise ConnectionError("Feishu connection lost")
                elif not self._process.is_alive() or (not connected and time.monotonic() > deadline):
                    raise ConnectionError("Feishu connection unavailable")
                else:
                    await asyncio.sleep(0.02)
        finally:
            self.stop_connection()

    def stop_connection(self):
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(2)
            if self._process.is_alive():
                raise RuntimeError("Feishu connection process could not be stopped")
            self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def send_text(self, context, text, *, idempotency_key):
        try:
            if context.get("app_id") != self.app_id or not text or len(text.encode()) > TEXT_BYTES:
                raise ValueError()
            chat_id = identifier(context.get("chat_id"))
            identifier(context.get("tenant_key"))
            identifier(idempotency_key)
            body = {
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "uuid": uuid.uuid5(uuid.NAMESPACE_URL, self.app_id + ":" + idempotency_key).hex
            }
            if context.get("thread_id"):
                url = API + "/im/v1/messages/" + identifier(context.get("message_id")) + "/reply"
                if context.get("reply_in_thread", True) is True:
                    body["reply_in_thread"] = True
                params = None
            else:
                url, params = API + "/im/v1/messages", {"receive_id_type": "chat_id"}
                body["receive_id"] = chat_id
        except (ValueError, TypeError):
            return DeliveryResult(outcome="rejected", error_type="invalid_reply_context")
        try:
            token = await self.token()
        except CallbackVerificationError:
            return DeliveryResult(outcome="rejected", error_type="authentication_failed", retryable=True)
        try:
            response = await self.http.post(url, params=params, json=body, headers={"Authorization": "Bearer " + token})
            if response.status_code == 429:
                raw_delay = response.headers.get("Retry-After", "2")
                delay = min(int(raw_delay), 3600) if raw_delay.isdigit() else 2
                return DeliveryResult(outcome="rejected", error_type="rate_limited", retryable=True, retry_after=delay)
            if response.status_code >= 500:
                return DeliveryResult(outcome="unknown", error_type="provider_outcome_unknown")
            data = response.json()
            if response.status_code == 200 and type(data.get("code")) is int and data["code"] == 0:
                message_id = identifier((data.get("data") or {}).get("message_id"))
                return DeliveryResult(outcome="accepted", receipt_kind="message_id", external_message_id=message_id)
            if response.status_code in {401, 403}:
                self._expires = 0
            if type(data.get("code")) is int and data["code"] != 0:
                return DeliveryResult(outcome="rejected", error_type="provider_rejected")
        except Exception:
            pass
        return DeliveryResult(outcome="unknown", error_type="transport_outcome_unknown")

    async def close(self):
        self.stop_connection()
        self._token, self._secret = None, None
        await self.http.aclose()
