"""Operators of the human-handoff area (collection `operators`). local-demo only: two seeded test accounts."""
import hashlib
import hmac
import secrets

DEMO_OPERATORS = (('ana', 'Ana', 'ana@atendimento.demo'), ('bruno', 'Bruno', 'bruno@atendimento.demo'))


def hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


class MongoOperatorRepository:
    def __init__(self, db): self.db = db

    async def ensure_indexes(self):
        await self.db.operators.create_index('email', unique=True)

    async def seed(self, workspace_id: str, password: str):
        """Upsert the demo accounts; the password follows OPERATOR_DEMO_PASSWORD on every start (fresh salt each time)."""
        for operator_id, name, email in DEMO_OPERATORS:
            salt = secrets.token_bytes(16)
            await self.db.operators.update_one({'email': email}, {'$set': {
                'id': operator_id, 'name': name, 'workspace_id': workspace_id, 'salt': salt.hex(), 'password_hash': hash_password(password, salt)}},
                upsert=True)

    async def verify(self, email: str, password: str) -> dict | None:
        row = await self.db.operators.find_one({'email': email.strip().lower()}, {'_id': 0})
        salt = bytes.fromhex(row['salt']) if row else secrets.token_bytes(16)  # same cost for unknown e-mails: no timing hint
        digest = hash_password(password, salt)
        if row and hmac.compare_digest(digest, row['password_hash']):
            return {k: row[k] for k in ('id', 'name', 'email', 'workspace_id')}
        return None
