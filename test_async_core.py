import pytest
import asyncio
import uuid
import os

os.environ["CORTEX_THALAMUS"] = "1"

@pytest.mark.asyncio
async def test_layer1_syntax_and_signatures():
    from cortex.pipeline.thalamus import thalamus
    from cortex.agents.spinal import SpinalAgent
    from cortex.db.vector import VectorMemory, COLLECTION_SHORT_TERM
    assert asyncio.iscoroutinefunction(thalamus)
    agent = SpinalAgent()
    assert asyncio.iscoroutinefunction(agent.process)

@pytest.mark.asyncio
async def test_layer2_io_stress():
    from cortex.db.cache import cache_set, cache_get
    from cortex.db.vector import VectorMemory, COLLECTION_SHORT_TERM
    
    sem = asyncio.Semaphore(5)
    
    async def worker(i):
        async with sem:
            # mix of cache and vector DB ops
            mock_session = f"test_session_{i}"
            vm = VectorMemory(session_id=mock_session, collection_name=COLLECTION_SHORT_TERM)
            text = f"stress test input {i}"
            await vm.store(text, {"test": True})
            await cache_set(text, f"response {i}", "CAT1", "1")
            
            res_cache = await cache_get(text)
            assert res_cache is not None, f"Cache get failed for {text}"
            
            res_vec = await vm.search(text, top_k=1)
            # assert we get something
            return True

    try:
        tasks = [worker(i) for i in range(50)]
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
        assert all(results)
    except Exception as e:
        pytest.fail(f"Layer 2 IO stress failed: {e}")

@pytest.mark.asyncio
async def test_layer3_e2e_thalamus():
    from cortex.pipeline.thalamus import thalamus
    
    input_str = "안녕" # < 20 chars
    # Ensure it returns immediately without blocking
    result = await asyncio.wait_for(thalamus(input_str), timeout=2.0)
    assert result is not None
    assert result["level"] == "1"
    assert result["cat"] == "CAT9"

if __name__ == "__main__":
    import pytest
    pytest.main(["-v", __file__])
