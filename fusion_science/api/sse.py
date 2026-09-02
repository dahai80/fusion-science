from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)


async def _sse_generator(tokens: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    # NOTE: do NOT accumulate the full streamed content. A long completion (large
    # paper draft) would hold the entire output in memory and echo it back in the
    # terminal "done" frame — an unbounded memory + payload leak. Clients reassemble
    # tokens themselves; the done frame only signals completion.
    try:
        async for token in tokens:
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        yield "data: " + json.dumps({"done": True}, ensure_ascii=False) + "\n\n"
    except Exception as e:
        # Log the real error server-side; send a generic message to the client so
        # internal exception details (paths, hostnames) are not leaked over SSE.
        logger.error("SSE generator error: %s", e)
        yield "data: " + json.dumps({"error": "stream_failed"}, ensure_ascii=False) + "\n\n"


def sse_response(tokens: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        _sse_generator(tokens),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
