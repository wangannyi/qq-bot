"""Fail closed on QQ: support answers are not permission to execute agent tools."""
import json
import logging
import time

import support_runtime
from openai.types.chat import ChatCompletion

logger = logging.getLogger(__name__)


def unavailable(request):
    return ChatCompletion(id='qq-support-unavailable', object='chat.completion',
        created=int(time.time()), model=request.get('model', 'configured-model'),
        choices=[{'index': 0, 'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': support_runtime.UNAVAILABLE_REPLY}}])


def execute(request, next_call, platform='', **kwargs):
    if platform != 'qqbot':
        return next_call(request)
    try:
        backend = support_runtime.settings().get('cie_support', {}).get('answer_backend')
        if backend == 'website':
            # Preserve the explicit legacy website-answer strategy; tool execution
            # remains disabled by tool_execute, and final output uses its evidence gate.
            return next_call(request)
        if backend != 'independent':
            return unavailable(request)
        # A configuration reload inside the runtime must not reopen the generic
        # provider path after this call was classified as independent support.
        return support_runtime.execute(request, unavailable, platform=platform, **kwargs)
    except Exception as exc:
        # Hermes middleware otherwise fails open to the generic provider callback.
        logger.warning('qq_support_execution_failed type=%s', type(exc).__name__)
        return unavailable(request)


def tool_execute(tool_name, args, next_call, platform='', **kwargs):
    if platform != 'qqbot':
        return next_call(args)
    return json.dumps({'error': 'QQ support profile does not execute agent tools. '
                      'Commands in an answer are instructions for the user, not operations.'})
