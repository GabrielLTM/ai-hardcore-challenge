"""Official-page collection with SSRF guards: https only, allowlisted hosts on every hop, public IPs only."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin
import httpx
from app.application.ports.knowledge import FetchedPage, SourceError

HTML_TYPES = ('text/html', 'application/xhtml+xml')


async def resolve_host(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split('%')[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class SafeFetcher:
    def __init__(self, allowed_hosts, *, transport=None, resolver=resolve_host, max_bytes=2_000_000,
                 timeout=15.0, max_redirects=3, user_agent='GetnetSupportKnowledgeBot/1.0'):
        self.allowed = {h.lower() for h in allowed_hosts}
        self.transport, self.resolver, self.max_bytes = transport, resolver, max_bytes
        self.timeout, self.max_redirects, self.user_agent = timeout, max_redirects, user_agent

    def _check(self, url: str) -> httpx.URL:
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            raise SourceError('blocked', 'URL inválida.') from None
        if parsed.scheme != 'https' or parsed.userinfo or parsed.port not in (None, 443):
            raise SourceError('blocked', 'Somente URLs https sem credenciais e na porta padrão são permitidas.')
        if parsed.host.lower() not in self.allowed:
            raise SourceError('blocked', f'O domínio {parsed.host} não está na lista de fontes oficiais.')
        return parsed

    async def _public_ip(self, host: str) -> str:
        try:
            addresses = await self.resolver(host)
        except OSError:
            raise SourceError('unreachable', f'Não foi possível resolver {host}.') from None
        if not addresses or not all(is_public(a) for a in addresses):
            raise SourceError('blocked', f'{host} resolve para um endereço não público.')
        return addresses[0]

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout, follow_redirects=False,
                                         headers={'User-Agent': self.user_agent, 'Accept': 'text/html'}) as client:
                for _ in range(self.max_redirects + 1):
                    target = self._check(url)
                    # Connect to the address that was validated (not a second DNS lookup) while keeping Host/SNI.
                    ip = await self._public_ip(target.host)
                    async with client.stream('GET', target.copy_with(host=ip), headers={'Host': target.host},
                                             extensions={'sni_hostname': target.host}) as response:
                        if response.is_redirect and response.headers.get('location'):
                            url = urljoin(url, response.headers['location'])
                            continue
                        return await self._read(url, response)
                raise SourceError('blocked', 'Redirecionamentos demais.')
        except httpx.HTTPError as exc:
            raise SourceError('unreachable', f'Fonte inacessível ({type(exc).__name__}).') from None

    async def _read(self, url: str, response: httpx.Response) -> FetchedPage:
        if response.status_code != 200:
            raise SourceError('unreachable', f'A fonte respondeu HTTP {response.status_code}.')
        content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if content_type not in HTML_TYPES:
            raise SourceError('unsupported_type', f'Tipo de conteúdo não suportado: {content_type or "desconhecido"}.')
        too_large = SourceError('too_large', f'A página excede o limite de {self.max_bytes} bytes.')
        if int(response.headers.get('content-length') or 0) > self.max_bytes:
            raise too_large
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > self.max_bytes:
                raise too_large
        return FetchedPage(url, content_type, bytes(body))
