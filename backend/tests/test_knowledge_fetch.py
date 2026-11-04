import httpx
import pytest
from app.adapters.outbound.web.extract import extract_markdown
from app.adapters.outbound.web.fetch import SafeFetcher, is_public
from app.application.ports.knowledge import SourceError
from knowledge_doubles import HOSTS, PUBLIC_IP, page, site

URL = 'https://site.getnet.com.br/pagina/'
HTML = {'content-type': 'text/html; charset=utf-8'}


async def code_of(fetcher, url=URL):
    with pytest.raises(SourceError) as error:
        await fetcher.fetch(url)
    return error.value.code


@pytest.mark.asyncio
async def test_fetch_connects_to_the_validated_address_keeping_host():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers['host'], request.extensions.get('sni_hostname')))
        return httpx.Response(200, headers=HTML, text='<p>ok</p>')

    result = await site(handler).fetch(URL)
    assert seen == [(PUBLIC_IP, 'site.getnet.com.br', 'site.getnet.com.br')]
    assert result.url == URL and result.body == b'<p>ok</p>'


@pytest.mark.asyncio
@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.5', '192.168.1.10', '169.254.169.254', '::1', 'fd00::1',
                                     '100.64.0.1', '::ffff:127.0.0.1', '0.0.0.0'])
async def test_ssrf_non_public_resolution_is_blocked_before_any_request(address):
    async def resolver(host):
        return [PUBLIC_IP, address]  # one private answer is enough to refuse

    called = []
    fetcher = SafeFetcher(HOSTS, resolver=resolver, transport=httpx.MockTransport(lambda r: called.append(r)))
    assert await code_of(fetcher) == 'blocked' and not called


def test_public_address_check():
    assert is_public('93.184.216.34') and not is_public('fe80::1%eth0') and not is_public('::ffff:10.0.0.1')


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['http://site.getnet.com.br/pagina/', 'https://evil.example.com/', 'https://127.0.0.1/',
                                 'https://site.getnet.com.br:8443/', 'https://user:pw@site.getnet.com.br/',
                                 'file:///etc/passwd', 'https://site.getnet.com.br.evil.com/'])
async def test_scheme_host_port_and_credentials_are_validated(url):
    assert await code_of(site('<p>x</p>'), url) == 'blocked'


@pytest.mark.asyncio
async def test_redirect_outside_allowlist_is_blocked():
    def handler(request):
        return httpx.Response(302, headers={'location': 'https://evil.example.com/steal'})

    assert await code_of(site(handler)) == 'blocked'


@pytest.mark.asyncio
async def test_redirect_to_http_or_internal_host_is_blocked():
    for location in ('http://site.getnet.com.br/', 'https://169.254.169.254/latest/meta-data'):
        assert await code_of(site(lambda r, location=location: httpx.Response(301, headers={'location': location}))) == 'blocked'


@pytest.mark.asyncio
async def test_allowlisted_relative_redirect_is_followed_and_loops_are_bounded():
    def handler(request):
        if request.url.path == '/pagina/':
            return httpx.Response(301, headers={'location': '/nova/'})
        return httpx.Response(200, headers=HTML, text='<p>destino</p>')

    result = await site(handler).fetch(URL)
    assert result.url == 'https://site.getnet.com.br/nova/' and b'destino' in result.body
    assert await code_of(site(lambda r: httpx.Response(302, headers={'location': '/'}))) == 'blocked'


@pytest.mark.asyncio
async def test_size_limit_applies_to_declared_and_streamed_bodies():
    fetcher = SafeFetcher(HOSTS, max_bytes=100, resolver=lambda h: _public(), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={**HTML, 'content-length': '5000'}, content=b'x' * 5000)))
    assert await code_of(fetcher) == 'too_large'
    streamed = SafeFetcher(HOSTS, max_bytes=100, resolver=lambda h: _public(), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers=HTML, stream=TwoChunks())))
    assert await code_of(streamed) == 'too_large'


class TwoChunks(httpx.AsyncByteStream):  # no content-length: the limit must be enforced while streaming
    async def __aiter__(self):
        yield b'x' * 60
        yield b'x' * 60


async def _public():
    return [PUBLIC_IP]


@pytest.mark.asyncio
async def test_unsupported_content_type_and_http_errors_are_classified():
    assert await code_of(site(lambda r: httpx.Response(200, headers={'content-type': 'application/pdf'}, content=b'%PDF'))) == 'unsupported_type'
    assert await code_of(site(lambda r: httpx.Response(404))) == 'unreachable'
    assert await code_of(site(lambda r: httpx.Response(503, headers=HTML))) == 'unreachable'


@pytest.mark.asyncio
async def test_network_errors_and_dns_failures_are_unreachable_without_leaking_details():
    def boom(request):
        raise httpx.ConnectError('secret internal detail')

    with pytest.raises(SourceError) as error:
        await site(boom).fetch(URL)
    assert error.value.code == 'unreachable' and 'secret' not in error.value.message

    async def no_dns(host):
        raise OSError('nxdomain')

    assert await code_of(SafeFetcher(HOSTS, resolver=no_dns)) == 'unreachable'


def test_extraction_drops_chrome_and_keeps_headings_as_sections():
    text = extract_markdown(page(('Antecipação', 'Solicite pelo aplicativo.'), ('Pix', 'Receba na hora.')))
    assert text == '# Página X\n\n## Antecipação\n\nSolicite pelo aplicativo.\n\n## Pix\n\nReceba na hora.'
    for noise in ('Menu', 'Rodapé', 'x()'):
        assert noise not in text


def test_extraction_of_a_page_without_text_is_empty():
    assert extract_markdown('<html><body><nav>só menu</nav><script>1</script></body></html>') == ''
