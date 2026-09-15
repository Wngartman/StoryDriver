import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.generation.model_provider import LMStudioClient, LMStudioError

class LocalTransport(unittest.IsolatedAsyncioTestCase):
    async def test_completed_result(self):
        client = LMStudioClient('http://127.0.0.1:12345/v1', provider_id='llama_cpp')
        client._ensure_local_model = AsyncMock()
        async def events(**kwargs):
            yield {'type': 'content', 'text': 'A scene.'}
            yield {'type': 'final_result', 'text': 'A scene.', 'classification': 'ok', 'stats': {'total_tokens': 3}}
        client.stream_scene_events = events
        result = await client.generate_scene_openai_compatible(model='fixture.gguf', system_prompt='', user_prompt='', parameters={})
        self.assertEqual(result['text'], 'A scene.')

    async def test_timeout_closes_stream(self):
        client = LMStudioClient('http://127.0.0.1:12345/v1', provider_id='llama_cpp')
        client._ensure_local_model = AsyncMock()
        closed = []
        async def events(**kwargs):
            try:
                yield {'type': 'content', 'text': '{'}
                await asyncio.sleep(5)
            finally:
                closed.append(True)
        client.stream_scene_events = events
        with self.assertRaises(LMStudioError):
            await client.generate_scene_openai_compatible(model='fixture.gguf', system_prompt='', user_prompt='', parameters={}, timeout=0.02)
        self.assertEqual(closed, [True])

if __name__ == '__main__': unittest.main()
