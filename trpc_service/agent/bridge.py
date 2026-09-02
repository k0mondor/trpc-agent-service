"""Bridge channel-neutral messages to the native tRPC-Agent Runner protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Callable
from enum import Enum

from pydantic import BaseModel
from pydantic import ConfigDict

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from trpc_service.channels import AttachmentRef
from trpc_service.channels import NormalizedInboundMessage
from trpc_service.tenant import ResolvedRoute

from .runtime import RunnerRegistry
from .runtime import tenant_app_name


class AgentChannelEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"


class AgentChannelEvent(BaseModel):
    """Small channel-facing projection of a native tRPC-Agent Event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: AgentChannelEventType
    event_id: str
    final: bool = False
    text: str | None = None
    tool_name: str | None = None
    error_type: str | None = None


class RunnerRouteMismatchError(RuntimeError):
    """Raised when a resolved Runner does not belong to the routed tenant application."""


def build_user_content(
    message: NormalizedInboundMessage,
    attachment_uri_resolver: Callable[[AttachmentRef], str] | None = None,
) -> Content:
    """Convert a verified IM message into the native tRPC-Agent Content type."""

    parts: list[Part] = []
    if message.text:
        parts.append(Part.from_text(text=message.text))
    for attachment in message.attachments:
        if attachment_uri_resolver is None:
            raise ValueError("attachment_uri_resolver is required for messages with attachments")
        parts.append(
            Part.from_uri(
                file_uri=attachment_uri_resolver(attachment),
                mime_type=attachment.mime_type,
            ))
    if not parts:
        raise ValueError("message must contain text or at least one attachment")
    return Content(role="user", parts=parts)


def project_agent_event(event: Event) -> tuple[AgentChannelEvent, ...]:
    """Project text/tool/error data while excluding model thought parts."""

    if event.error_code or event.error_message:
        return (
            AgentChannelEvent(
                type=AgentChannelEventType.ERROR,
                event_id=event.id,
                final=True,
                text="Agent execution failed",
                error_type="agent_error",
            ),
        )

    projected: list[AgentChannelEvent] = []
    if not event.content or not event.content.parts:
        return ()

    for part in event.content.parts:
        if part.text and not getattr(part, "thought", False):
            projected.append(
                AgentChannelEvent(
                    type=(AgentChannelEventType.TEXT_DELTA if event.partial else AgentChannelEventType.TEXT),
                    event_id=event.id,
                    final=event.is_final_response(),
                    text=part.text,
                ))
        elif part.function_call:
            projected.append(
                AgentChannelEvent(
                    type=AgentChannelEventType.TOOL_CALL,
                    event_id=event.id,
                    tool_name=part.function_call.name,
                ))
        elif part.function_response:
            projected.append(
                AgentChannelEvent(
                    type=AgentChannelEventType.TOOL_RESULT,
                    event_id=event.id,
                    tool_name=part.function_response.name,
                ))
    return tuple(projected)


def build_agent_context(
    route: ResolvedRoute,
    message: NormalizedInboundMessage,
    agent_context: AgentContext | None = None,
) -> AgentContext:
    """Create or enrich AgentContext with trusted platform metadata."""

    context = agent_context or AgentContext()
    platform_metadata = {
        "tenant_id": route.tenant_id,
        "agent_app_id": route.agent_app_id,
        "config_version": route.config_version,
        "channel_binding_id": route.channel_binding_id,
        "request_id": message.request_id,
        "external_message_id": message.external_message_id,
    }
    for key, value in platform_metadata.items():
        if key in context.metadata and context.metadata[key] != value:
            raise ValueError(f"agent context metadata conflicts with trusted {key}")
        context.with_metadata(key, value)
    return context


async def run_normalized_message(
    *,
    runner_registry: RunnerRegistry,
    route: ResolvedRoute,
    message: NormalizedInboundMessage,
    agent_context: AgentContext | None = None,
    attachment_uri_resolver: Callable[[AttachmentRef], str] | None = None,
) -> AsyncIterator[AgentChannelEvent]:
    """Execute one routed IM message through the real tRPC-Agent Runner API."""

    runner = runner_registry.resolve(route.tenant_id, route.agent_app_id, route.config_version)
    expected_app_name = tenant_app_name(route.tenant_id, route.agent_app_id)
    if runner.app_name != expected_app_name:
        raise RunnerRouteMismatchError(
            f"runner app_name does not match routed tenant application: expected {expected_app_name}")
    content = build_user_content(message, attachment_uri_resolver)
    context = build_agent_context(route, message, agent_context)
    async for event in runner.run_async(
        user_id=route.internal_user_id,
        session_id=route.session_id,
        new_message=content,
        agent_context=context,
    ):
        for projected in project_agent_event(event):
            yield projected
