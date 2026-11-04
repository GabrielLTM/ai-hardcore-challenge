"""Hosted web search through the Responses API. Unavailability is a result, not an exception."""
import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class OpenAIWebSearch:
    def __init__(self, client, model, timeout_s: float = 90.0):  # busca hospedada passa de 30 s em perguntas abertas (medido: 44 s)
        self.client, self.model, self.timeout_s = client, model, timeout_s

    async def __call__(self, query):
        retrieved_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        try:
            # Hosted web search is billed per call by OpenAI, on top of the token cost of the request.
            response = await asyncio.wait_for(self.client.responses.create(
                model=self.model, input=query, tools=[{'type': 'web_search'}],
                include=['web_search_call.action.sources']), self.timeout_s)
            answer = (response.output_text or '').strip()
            if not answer:
                raise ValueError('empty answer')
            sources = self._sources(response)
        except Exception as exc:  # timeout, provider error, malformed response: never invent sources
            log.warning('web_search unavailable: %s', type(exc).__name__)
            return {'status': 'unavailable', 'answer': '', 'sources': [], 'retrieved_at': retrieved_at}
        return {'status': 'ok', 'answer': answer, 'sources': sources, 'retrieved_at': retrieved_at}

    @staticmethod
    def _sources(response) -> list[dict]:
        """Sources the provider reported (search actions) and cited (annotations), deduplicated by URL."""
        found: dict[str, dict] = {}
        for item in response.output:
            if item.type == 'web_search_call':
                for source in getattr(getattr(item, 'action', None), 'sources', None) or []:
                    if getattr(source, 'url', None):
                        found.setdefault(source.url, {'url': source.url})
                    elif getattr(source, 'name', None):  # provider data API without a URL, e.g. weather ('oai-weather')
                        found.setdefault(source.name, {'url': None, 'title': f'Serviço de dados do provedor de busca ({source.name})'})
            elif item.type == 'message':
                for part in item.content:
                    for note in getattr(part, 'annotations', None) or []:
                        if note.type == 'url_citation' and note.url:
                            entry = found.setdefault(note.url, {'url': note.url})
                            if getattr(note, 'title', None):
                                entry['title'] = note.title
        return list(found.values())
