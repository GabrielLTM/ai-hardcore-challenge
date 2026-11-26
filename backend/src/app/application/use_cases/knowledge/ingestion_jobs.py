"""Run one claimed ingestion job: heartbeat keeps the lease, a timeout bounds the attempt, failures retry."""
import asyncio
from app.application.ports.knowledge import LeaseLost


def job_outcome(results: list[dict]) -> tuple[str, dict | None]:
    failed = [r for r in results if r['outcome'] == 'failed']
    status = 'failed' if failed and len(failed) == len(results) else 'partial' if failed else 'succeeded'
    return status, ({'source_id': failed[0]['source_id'], **failed[0]['error']} if failed else None)


async def process_next(jobs, pipeline, load_catalog, worker_id: str, *, lease_s: float = 60.0,
                       timeout_s: float = 600.0, retry_delay_s: float = 30.0) -> bool:
    """Claim and process one job. Returns False when the queue had nothing eligible."""
    job = await jobs.claim(worker_id, lease_s)
    if job is None:
        return False
    lost = False

    async def report(phase, status, **counts):
        await jobs.set_phase(job['id'], worker_id, phase, status, **counts)

    async def heartbeat():
        nonlocal lost
        while True:
            await asyncio.sleep(lease_s / 3)
            if not await jobs.heartbeat(job['id'], worker_id, lease_s):
                lost = True
                work.cancel()
                return

    async def retry(code, message):
        try:
            await jobs.retry_or_fail(job['id'], worker_id, code, message, retry_delay_s)
        except LeaseLost:
            pass

    async def execute():  # inside the task so a broken catalog goes through the retry path below
        return await pipeline.run(job['workspace_id'], job['id'], load_catalog(), job['source_ids'], report)

    work = asyncio.ensure_future(execute())
    beat = asyncio.ensure_future(heartbeat())
    try:
        results = await asyncio.wait_for(work, timeout_s)
        status, error = job_outcome(results)
        await jobs.finish(job['id'], worker_id, status, results, error)
    except asyncio.TimeoutError:
        await retry('timeout', 'O processamento excedeu o tempo limite.')
    except asyncio.CancelledError:
        if not lost:  # cancelled by shutdown, not by a lost lease: the lease will expire and the job is retried
            raise
    except LeaseLost:
        pass  # another worker owns the job now; its writes win
    except Exception as exc:
        await retry('internal', f'Falha inesperada no processamento ({type(exc).__name__}).')
    finally:
        beat.cancel()
    return True
