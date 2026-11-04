import os


def pytest_configure(config):
    # Tests never reach the real OpenAI API: an empty variable beats the key in the root .env (subprocesses inherit it).
    # The opt-in real evaluation run sets RUN_REAL_OPENAI=1 to keep the key.
    if os.environ.get('RUN_REAL_OPENAI') != '1':
        os.environ['OPENAI_API_KEY'] = ''
