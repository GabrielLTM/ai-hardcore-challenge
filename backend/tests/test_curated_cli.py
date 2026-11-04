import os
import subprocess
import sys
from pathlib import Path


def test_cli_requires_key_without_leaking_secret():
    env = {**os.environ, 'OPENAI_API_KEY': '', 'PYTHONPATH': 'src'}  # empty value wins over a key in the root .env
    result = subprocess.run([sys.executable, '-m', 'app.adapters.inbound.cli.ingest_curated', '../data/knowledge/getnet'],
                            cwd=Path(__file__).parents[1], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'OPENAI_API_KEY' in result.stderr


def test_url_ingestion_cli_requires_key_without_leaking_secret():
    env = {**os.environ, 'OPENAI_API_KEY': '', 'PYTHONPATH': 'src'}  # empty value wins over any .env file
    result = subprocess.run([sys.executable, '-m', 'app.adapters.inbound.cli.ingest_urls'],
                            cwd=Path(__file__).parents[1], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and 'OPENAI_API_KEY' in result.stderr and 'Traceback' not in result.stderr
