from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator

from starlette.requests import Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# F-P9: bounded queue so a slow client cannot force the server to buffer an
# unbounded token stream in memory. When the queue fills, the producer blocks
# (natural backpressure); if the client disconnects, we cancel the producer.
_SSE_QUEUE_MAXSIZE = 256


async def _sse_generator(
    tokens: AsyncGenerator[str, None],
    request: Request | None = None,
) -> AsyncGenerator[str, None]:
    # NOTE: do NOT accumulate the full streamed content. A long completion (large
    # paper draft) would hold the entire output in memory and echo it back in the
    # terminal "done" frame — an unbounded memory + payload leak. Clients reassemble
    # tokens themselves; the done frame only signals completion.
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
    SENTINEL = None

    async def produce() -> None:
        try:
            async for token in tokens:
                await queue.put(token)
        except Exception as e:
            logger.error("SSE producer error: %s", e)
            await queue.put(f"\x00error:{e}")
        finally:
            await queue.put(SENTINEL)

    producer = asyncio.create_task(produce())
    try:
        while True:
            # F-P9: detect client disconnect between frames so we stop paying
            # for LLM tokens nobody is reading.
            if request is not None:
                try:
                    if await request.is_disconnected():
                        logger.info("SSE client disconnected; cancelling stream")
                        break
                except Exception:
                    pass
            try:
                item = await asyncio.wait_for(queue.get(), timeout=5.0)
            except TimeoutError:
                # no token in 5s — loop back to re-check disconnect
                continue
            if item is SENTINEL:
                yield "data: " + json.dumps({"done": True}, ensure_ascii=False) + "\n\n"
                break
            if isinstance(item, str) and item.startswith("\x00error:"):
                yield "data: " + json.dumps({"error": "stream_failed"}, ensure_ascii=False) + "\n\n"
                break
            yield f"data: {json.dumps({'token': item}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error("SSE generator error: %s", e)
        yield "data: " + json.dumps({"error": "stream_failed"}, ensure_ascii=False) + "\n\n"
    finally:
        if not producer.done():
            producer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await producer


def sse_response(tokens: AsyncGenerator[str, None], request: Request | None = None) -> StreamingResponse:
    return StreamingResponse(
        _sse_generator(tokens, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
