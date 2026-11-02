"""Read public source pages without sending any model credential."""
import ipaddress
import socket
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
import httpx


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError("Only public HTTP(S) source URLs on standard ports are supported")
    for info in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(info[4][0]).is_global:raise ValueError("Local/private source addresses are not supported")
    return url


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []; self.main_parts = []; self.main_depth = 0; self.in_title = False; self.title_parts = [];self.publication_metadata={};self.ld_json=None
    def handle_starttag(self, tag, attrs):
        if tag=='script' and dict(attrs).get('type','').lower()=='application/ld+json':self.ld_json=[]
        if tag=='meta':
            attrs=dict(attrs);key=(attrs.get('property') or attrs.get('name') or '').lower()
            if key in ('article:published_time','datepublished','pubdate') and attrs.get('content'):
                self.publication_metadata['published_at']=attrs['content']
                self.publication_metadata['publication_time_source']='publisher_html_metadata_unverified'
            if key in ('article:modified_time','datemodified') and attrs.get('content'):
                self.publication_metadata['publisher_modified_at']=attrs['content']
        if tag in ("script", "style", "noscript", "svg"):self.skip += 1
        if tag in ("main","article"):self.main_depth += 1
        if tag == "title":self.in_title = True
        if not self.skip and tag in ("p", "div", "h1", "h2", "h3", "li", "br", "article"):
            self.parts.append("\n")
            if self.main_depth:self.main_parts.append("\n")
    def handle_endtag(self, tag):
        if tag=='script' and self.ld_json is not None:
            try:self._structured_metadata(json.loads(''.join(self.ld_json)))
            except (ValueError,TypeError):pass
            self.ld_json=None
        if tag in ("script", "style", "noscript", "svg") and self.skip:self.skip -= 1
        if tag in ("main","article") and self.main_depth:self.main_depth -= 1
        if tag == "title":self.in_title = False
    def handle_data(self, data):
        if self.ld_json is not None:self.ld_json.append(data)
        if not self.skip:
            self.parts.append(data)
            if self.main_depth:self.main_parts.append(data)
            if self.in_title:self.title_parts.append(data)

    def text(self):
        return "\n".join(" ".join(x.split()) for x in "".join(self.main_parts or self.parts).splitlines() if x.strip())

    def title(self):return " ".join("".join(self.title_parts).split())

    def _structured_metadata(self,node):
        if isinstance(node,list):
            for entry in node:self._structured_metadata(entry)
        elif isinstance(node,dict):
            kinds=node.get('@type',[]);kinds=[kinds] if isinstance(kinds,str) else kinds
            article=next((t for t in kinds if t in ('Article','NewsArticle','BlogPosting','ReportageNewsArticle')),None)
            if article:
                self.publication_metadata['document_type']=article
                if isinstance(node.get('datePublished'),str):
                    self.publication_metadata.setdefault('published_at',node['datePublished'])
                    self.publication_metadata.setdefault('publication_time_source','publisher_schema_org_metadata_unverified')
                if isinstance(node.get('dateModified'),str):self.publication_metadata.setdefault('publisher_modified_at',node['dateModified'])
            for value in node.values():
                if isinstance(value,(list,dict)):self._structured_metadata(value)


def fetch_page(url):
    with httpx.Client(timeout=20, trust_env=True, follow_redirects=False) as client:
        for _ in range(5):
            public_url(url)
            with client.stream("GET", url, headers={"User-Agent": "LocalETHResearch/0.2", "Accept": "text/html,text/plain,application/json"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"]); continue
                if response.status_code != 200:raise RuntimeError(f"Source page HTTP {response.status_code}; use another source")
                content_type = response.headers.get("content-type", "")
                if not any(x in content_type for x in ("html", "text", "json", "xml")):raise ValueError("Source reader supports text/HTML/JSON/XML; not PDF or video")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:raise ValueError("Source page exceeds 2 MB")
            decoded = bytes(raw).decode("utf-8", errors="replace")
            if "html" in content_type:
                parser = PageText(); parser.feed(decoded)
                text = parser.text(); title = parser.title();metadata=parser.publication_metadata
            else:text = decoded; title = url;metadata={}
            return {"url": url, "title":title,"raw": bytes(raw), "text": text[:60000], "text_truncated": len(text) > 60000, "content_type": content_type,**metadata}
    raise RuntimeError("Too many source redirects")
