"""WeCom public WebSocket client integration; message frames never live in a per-chat cache."""

from datetime import datetime, timezone
import uuid

from wecom_aibot_sdk import WSClient
from wecom_aibot_sdk.types.api import WsFrame

from .base import CallbackVerificationError
from .events import DeliveryResult, TransportEvent, action_command
from .models import NormalizedInboundMessage


class QuietProtocolLogger:
    """The dependency logs entire auth and message frames; only platform events are exported."""

    def debug(self, *args, **kwargs):
        pass

    info = debug
    warn = debug
    warning = debug
    error = debug


class WecomAdapter:

    def __init__(self, client: WSClient):
        self.client = client

    @classmethod
    def create(cls, bot_id: str, secret: str):
        return cls(
            WSClient({
                "bot_id": bot_id,
                "secret": secret,
                "logger": QuietProtocolLogger(),
                "max_reconnect_attempts": 1,
                "reconnect_interval": 60000
            }))

    def normalize(self, frame: WsFrame, binding) -> TransportEvent:
        if binding.channel.value != "wecom" or not self.client.is_authenticated:
            raise CallbackVerificationError("wecom connection is not authenticated")
        body = frame.body
        if not isinstance(body, dict) or body.get("aibotid") != binding.external_account_id:
            raise CallbackVerificationError("wecom message does not match authenticated account")
        try:
            event_id = body["msgid"]
            actor = body["from"]["userid"]
            chat = str(body.get("chatid") or actor)
            if not isinstance(event_id, str) or not event_id or not isinstance(actor, str) or not actor:
                raise ValueError("missing identity")
            context = {"req_id": frame.headers["req_id"], "chat_id": chat}
            if frame.cmd == "aibot_event_callback":
                event = body.get("event", {})
                if event.get("eventtype") == "template_card_event" and event.get("event_key"):
                    return TransportEvent(event_id=event_id,
                                          kind="action",
                                          external_actor_id=actor,
                                          external_chat_id=chat,
                                          action_data=event["event_key"],
                                          reply_context=context)
                return TransportEvent(event_id=event_id, kind="ignored", reason="unsupported_event")
            if frame.cmd != "aibot_msg_callback" or body.get("msgtype") != "text":
                return TransportEvent(event_id=event_id, kind="ignored", reason="unsupported_message")
            chat_type = body.get("chattype")
            if chat_type not in {"single", "group"} or (chat_type == "group" and not body.get("chatid")):
                raise ValueError("invalid conversation")
            normalized = NormalizedInboundMessage(channel="wecom",
                                                  webhook_public_id=binding.webhook_public_id,
                                                  external_message_id=event_id,
                                                  external_user_id=actor,
                                                  external_chat_id=chat,
                                                  conversation_type="direct" if chat_type == "single" else "group",
                                                  text=body["text"]["content"],
                                                  received_at=datetime.now(timezone.utc),
                                                  request_id=uuid.uuid4().hex)
            return action_command(normalized, context) or TransportEvent(event_id=event_id,
                                                                         kind="chat",
                                                                         message=normalized,
                                                                         external_actor_id=actor,
                                                                         external_chat_id=chat,
                                                                         reply_context=context)
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid wecom event") from None

    async def send_text(self, context: dict, text: str, *, stream_id: str, final: bool = True) -> DeliveryResult:
        if not self.client.is_authenticated:
            return DeliveryResult(outcome="rejected", error_type="not_connected", retryable=True)
        try:
            reply = await self.client.reply_stream({"req_id": context["req_id"]}, stream_id, text, finish=final)
            if reply.errcode == 0:
                return DeliveryResult(outcome="accepted",
                                      receipt_kind="protocol_ack",
                                      provider_request_id=reply.headers.get("req_id"))
            return DeliveryResult(outcome="rejected", error_type="platform_rejected")
        except Exception:
            # The SDK embeds raw protocol data in exceptions. A lost ACK is not a safe retry signal.
            return DeliveryResult(outcome="unknown", error_type="transport_outcome_unknown")

    async def close(self):
        await self.client.disconnect()
