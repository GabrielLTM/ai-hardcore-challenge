"""HTML to Markdown-like text: boilerplate removed, headings kept as `#` lines for section chunking."""
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DROP = ('script', 'style', 'noscript', 'template', 'svg', 'iframe', 'nav', 'footer', 'aside', 'form', 'button', 'head')
INLINE = {'a', 'abbr', 'b', 'bdi', 'bdo', 'cite', 'code', 'data', 'em', 'i', 'kbd', 'label', 'mark', 'q', 's',
          'small', 'span', 'strong', 'sub', 'sup', 'time', 'u'}
# Site-wide header and landmark roles are navigation chrome; <header> inside an article keeps its title.
SITE_CHROME = 'body > header, [role=navigation], [role=banner], [role=contentinfo], [aria-hidden=true]'
HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


def content_root(soup: BeautifulSoup) -> Tag:
    """<main> when present; otherwise an <article> only if it holds most of the page text.

    Pages without <main> often use <article> for small cards (a single teaser), and taking the first one
    dropped the real content and reported the page as empty."""
    if main := soup.find('main'):
        return main
    body = soup.body or soup
    total = len(body.get_text(' ', strip=True))
    articles = sorted(soup.find_all('article'), key=lambda a: len(a.get_text(' ', strip=True)), reverse=True)
    if articles and len(articles[0].get_text(' ', strip=True)) >= total / 2:
        return articles[0]
    return body


def extract_markdown(body: bytes | str) -> str:
    soup = BeautifulSoup(body, 'html.parser')
    for tag in [*soup.find_all(DROP), *soup.select(SITE_CHROME)]:
        if not tag.decomposed:
            tag.decompose()
    root = content_root(soup)
    out: list[str] = []
    buffer: list[str] = []

    def flush():
        text = ' '.join(''.join(buffer).split())
        buffer.clear()
        if text:
            out.append(text)

    def walk(node: Tag):
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buffer.append(str(child))
            elif child.name in INLINE:
                walk(child)
            elif child.name == 'br':
                flush()
            elif child.name in HEADINGS:
                flush()
                title = ' '.join(child.get_text(' ').split())
                if title:
                    out.append(f"{'#' * int(child.name[1])} {title}")
            else:
                flush()
                walk(child)
                flush()

    walk(root)
    flush()
    return '\n\n'.join(out)
