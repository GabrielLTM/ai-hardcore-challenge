"""Durable ingestion queue in Mongo: lease with heartbeat, bounded retries, per-phase state."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pymongo import ReturnDocument
from app.application.ports.knowledge import LeaseLost
from app.domain.knowledge import PHASES

HIDDEN = {'_id': 0, 'lease_owner': 0}


def utcnow():
    return datetime.now(timezone.utc)


def initial_phases():
    return {phase: {'status': 'pending'} for phase in PHASES}


def running_phase(job):
    return next((p for p in PHASES if job['phases'][p]['status'] == 'running'), None)


class MongoIngestionJobs:
    def __init__(self, db, clock=utcnow):
        self.jobs, self.clock = db.ingestion_jobs, clock

    async def ensure_indexes(self):
        await self.jobs.create_index([('workspace_id', 1), ('id', 1)], unique=True)
        await self.jobs.create_index([('workspace_id', 1), ('created_at', -1)])
        await self.jobs.create_index([('status', 1), ('available_at', 1)])

    async def create(self, workspace_id, requested_by, source_ids, max_attempts=3):
        now = self.clock()
        job = {'id': uuid4().hex, 'workspace_id': workspace_id, 'requested_by': requested_by,
               'source_ids': source_ids, 'status': 'queued', 'phases': initial_phases(), 'results': [], 'error': None,
               'attempts': 0, 'max_attempts': max_attempts, 'lease_owner': None, 'lease_expires_at': None,
               'created_at': now, 'available_at': now, 'started_at': None, 'finished_at': None}
        await self.jobs.insert_one(job)
        return {k: v for k, v in job.items() if k not in ('_id', 'lease_owner')}

    async def get(self, workspace_id, job_id):
        return await self.jobs.find_one({'workspace_id': workspace_id, 'id': job_id}, HIDDEN)

    async def recent(self, workspace_id, limit=20):
        cursor = self.jobs.find({'workspace_id': workspace_id}, HIDDEN).sort('created_at', -1)
        return await cursor.to_list(length=limit)

    async def claim(self, worker_id, lease_s):
        """Take the oldest queued job, or one whose worker stopped renewing its lease; one claim per job at a time."""
        now = self.clock()
        exhausted = {'status': 'running', 'lease_expires_at': {'$lt': now}, '$expr': {'$gte': ['$attempts', '$max_attempts']}}
        async for job in self.jobs.find(exhausted):
            error = {'phase': running_phase(job), 'code': 'lease_expired',
                     'message': 'O worker parou de responder e as tentativas acabaram.'}
            await self.jobs.update_one(
                {'id': job['id'], 'status': 'running', 'lease_expires_at': {'$lt': now}},
                {'$set': {'status': 'failed', 'error': error, 'finished_at': now,
                          'lease_owner': None, 'lease_expires_at': None}})
        return await self.jobs.find_one_and_update(
            {'$or': [{'status': 'queued', 'available_at': {'$lte': now}},
                     {'status': 'running', 'lease_expires_at': {'$lt': now}}]},
            {'$set': {'status': 'running', 'lease_owner': worker_id, 'lease_expires_at': now + timedelta(seconds=lease_s),
                      'heartbeat_at': now, 'started_at': now, 'phases': initial_phases(), 'error': None},
             '$inc': {'attempts': 1}},
            sort=[('created_at', 1)], return_document=ReturnDocument.AFTER, projection=HIDDEN)

    def _owned(self, job_id, worker_id):
        return {'id': job_id, 'lease_owner': worker_id, 'status': 'running'}

    async def heartbeat(self, job_id, worker_id, lease_s) -> bool:
        now = self.clock()
        result = await self.jobs.update_one(self._owned(job_id, worker_id), {
            '$set': {'heartbeat_at': now, 'lease_expires_at': now + timedelta(seconds=lease_s)}})
        return result.matched_count == 1

    async def set_phase(self, job_id, worker_id, phase, status, **counts):
        at = self.clock()
        fields = {'status': status, **counts, 'started_at' if status == 'running' else 'finished_at': at}
        result = await self.jobs.update_one(self._owned(job_id, worker_id),
                                            {'$set': {f'phases.{phase}.{k}': v for k, v in fields.items()}})
        if result.matched_count == 0:
            raise LeaseLost(job_id)

    async def finish(self, job_id, worker_id, status, results, error=None):
        result = await self.jobs.update_one(self._owned(job_id, worker_id), {'$set': {
            'status': status, 'results': results, 'error': error, 'finished_at': self.clock(),
            'lease_owner': None, 'lease_expires_at': None}})
        if result.matched_count == 0:
            raise LeaseLost(job_id)

    async def retry_or_fail(self, job_id, worker_id, code, message, delay_s):
        """Unexpected failure or timeout: requeue with backoff while attempts remain, otherwise fail with the phase."""
        job = await self.jobs.find_one(self._owned(job_id, worker_id))
        if job is None:
            raise LeaseLost(job_id)
        error, now = {'phase': running_phase(job), 'code': code, 'message': message}, self.clock()
        if job['attempts'] >= job['max_attempts']:
            return await self.finish(job_id, worker_id, 'failed', job['results'], error)
        await self.jobs.update_one(self._owned(job_id, worker_id), {'$set': {
            'status': 'queued', 'lease_owner': None, 'lease_expires_at': None, 'error': error,
            'available_at': now + timedelta(seconds=delay_s * job['attempts'])}})
