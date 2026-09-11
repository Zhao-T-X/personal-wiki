from pathlib import Path

SUPPORTED = {'.md':'markdown','.markdown':'markdown','.txt':'text','.text':'text','.html':'html','.htm':'html'}


def read_file(path: str) -> tuple[str,str]:
    p=Path(path)
    ext=p.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f'Unsupported file type: {ext}. Supported: {sorted(SUPPORTED)}')
    text=p.read_text(encoding='utf-8',errors='replace')
    if SUPPORTED[ext]=='html':
        try:
            from html.parser import HTMLParser
            class P(HTMLParser):
                def __init__(self): super().__init__(); self.parts=[]
                def handle_data(self,data): self.parts.append(data)
            parser=P(); parser.feed(text); text='\n'.join(parser.parts)
        except Exception:
            pass
    return p.name, text
