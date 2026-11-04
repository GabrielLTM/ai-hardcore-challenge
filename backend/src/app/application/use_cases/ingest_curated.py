"""Ingest the curated corpus from its manifest; provenance remains attached to every chunk."""
from pathlib import Path
from app.application.use_cases.ingest_sources import IngestionPipeline
from app.application.use_cases.knowledge_catalog import curated_sources, read_curated_corpus  # noqa: F401 (re-export)


async def ingest_curated(root: Path, gateway, repository, embedding_model: str, workspace_id: str, *,
                         max_chars: int = 1200, overlap: int = 150, dimensions: int | None = None) -> int:
    catalog = {s['id']: s for s in curated_sources(root)}
    await repository.register_sources(workspace_id, list(catalog.values()))
    pipeline = IngestionPipeline(repository=repository, gateway=gateway, embedding_model=embedding_model,
                                 curated_root=root, dimensions=dimensions, max_chars=max_chars, overlap=overlap)
    results = await pipeline.run(workspace_id, None, catalog, list(catalog))
    failed = [r for r in results if r['outcome'] == 'failed']
    if failed:
        raise RuntimeError('; '.join(f"{r['source_id']}: {r['error']['message']}" for r in failed))
    return len(results)
