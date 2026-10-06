"""Convert submission.md -> submission.docx (stdlib + python-markdown), for import into Google Docs.

Supports what submission.md uses: headings, paragraphs, bold/italic/inline code, links, bullet and
numbered lists, tables, fenced code blocks, blockquotes, horizontal rules.
Usage: python3 md_to_docx.py submission.md submission.docx
"""
import sys
import zipfile
from html import escape
from html.parser import HTMLParser

import markdown

MONO = "Courier New"   # ships with both Word and Google Docs
BLUE = "0052FF"


def run_xml(text, bold=False, italic=False, code=False, link=False):
    if not text:
        return ""
    props = []
    if code:
        props.append(f'<w:rFonts w:ascii="{MONO}" w:hAnsi="{MONO}" w:cs="{MONO}"/>')
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if link:
        props.append(f'<w:color w:val="{BLUE}"/><w:u w:val="single"/>')
    if code:
        props.append('<w:shd w:val="clear" w:color="auto" w:fill="F0F2F5"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text, quote=False)}</w:t></w:r>'


class DocxBuilder(HTMLParser):
    """Walks python-markdown's HTML and emits WordprocessingML body XML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body, self.links = [], []
        self.runs = []                 # runs of the paragraph being built
        self.fmt = {"bold": 0, "italic": 0, "code": 0}
        self.href = None
        self.block = None              # current paragraph style id
        self.lists = []                # stack of ["ul"|"ol", counter]
        self.in_pre = False
        self.pre_text = []
        self.quote = 0
        self.table = None              # list of rows; row = list of (is_header, cell_paragraph_xml)
        self.cell = None
        self.list_kinds = []
        self.keep_rows = False

    # ---- paragraph helpers -------------------------------------------------
    def para(self, style=None, runs="", ind=None, shade=None, keep_next=False, spacing=None,
             numpr=None, border=None):
        # children in CT_PPr schema order: pStyle, keepNext, numPr, pBdr, shd, spacing, ind
        ppr = []
        if style:
            ppr.append(f'<w:pStyle w:val="{style}"/>')
        if keep_next:
            ppr.append("<w:keepNext/>")
        if numpr:
            ppr.append(numpr)
        if border:
            ppr.append(border)
        if shade:
            ppr.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
        if spacing:
            before, after = spacing
            ppr.append(f'<w:spacing w:before="{before}" w:after="{after}" w:line="240" w:lineRule="auto"/>')
        if ind:
            ppr.append(ind)
        return f"<w:p><w:pPr>{''.join(ppr)}</w:pPr>{runs}</w:p>"

    def start_para(self, style=None):
        self.flush()
        self.block = style or "Normal"
        self.runs = []

    def flush(self):
        if self.block is None:
            return
        runs = "".join(self.runs)
        if self.cell is not None:
            self.cell.append(self.para("TableText", runs, keep_next=self.keep_rows))
        elif self.lists:
            num_id = self.lists[-1][2]
            numpr = f'<w:numPr><w:ilvl w:val="{len(self.lists) - 1}"/><w:numId w:val="{num_id}"/></w:numPr>'
            self.body.append(self.para("ListText", runs, numpr=numpr))
        elif self.quote:
            border = f'<w:pBdr><w:left w:val="single" w:sz="24" w:space="8" w:color="{BLUE}"/></w:pBdr>'
            self.body.append(self.para(self.block, runs, ind='<w:ind w:left="360"/>', border=border, shade="F5F8FF"))
        elif runs or self.block.startswith("Heading"):
            # headings, and bold-only lead-ins like "**Layers**", stay with what follows
            lead_in = all("<w:b/>" in r for r in self.runs) and self.block == "Normal"
            self.body.append(self.para(self.block, runs, keep_next=self.block.startswith("Heading") or lead_in))
        self.block, self.runs = None, []

    # ---- HTML events ---------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h1", "h2", "h3"):
            self.start_para({"h1": "Title", "h2": "Heading2", "h3": "Heading3"}[tag])
        elif tag == "p":
            self.start_para("Normal")
        elif tag in ("ul", "ol"):
            self.flush()
            self.list_kinds.append(tag)             # one w:num per list, so each <ol> restarts at 1
            self.lists.append([tag, 0, len(self.list_kinds)])
        elif tag == "li":
            self.flush()
            self.lists[-1][1] += 1
            self.block, self.runs = "ListText", []
        elif tag == "blockquote":
            self.flush()
            self.quote += 1
        elif tag == "pre":
            self.flush()
            self.in_pre, self.pre_text = True, []
        elif tag in ("strong", "b"):
            self.fmt["bold"] += 1
        elif tag in ("em", "i"):
            self.fmt["italic"] += 1
        elif tag == "code" and not self.in_pre:
            self.fmt["code"] += 1
        elif tag == "a":
            self.href = a.get("href")
        elif tag == "hr":
            self.flush()
            self.body.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" '
                             'w:color="D0D5DD"/></w:pBdr></w:pPr></w:p>')
        elif tag == "table":
            self.flush()
            self.table = []
            self.keep_rows = True
        elif tag == "tr":
            self.table.append([])
            self.keep_rows = len(self.table) <= 2    # header + first row stay with the heading above
        elif tag in ("td", "th"):
            self.cell = []
            self.cell_header = tag == "th"
            if self.cell_header:
                self.fmt["bold"] += 1
            self.block, self.runs = "TableText", []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "p"):
            if not self.lists or self.cell is not None:
                self.flush()
            else:
                # paragraph inside a list item: keep building the same item
                pass
        elif tag == "li":
            self.flush()
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.pop()
        elif tag == "blockquote":
            self.flush()
            self.quote -= 1
        elif tag == "pre":
            self.in_pre = False
            lines = "".join(self.pre_text).rstrip("\n").split("\n")
            short = len(lines) <= 25                 # short blocks never split across pages
            for i, line in enumerate(lines):
                last = i == len(lines) - 1
                self.body.append(self.para("Code", run_xml(line or " "), shade="F6F8FA",
                                           keep_next=short and not last,
                                           spacing=(120 if i == 0 else 0, 160 if last else 0)))
        elif tag in ("strong", "b"):
            self.fmt["bold"] -= 1
        elif tag in ("em", "i"):
            self.fmt["italic"] -= 1
        elif tag == "code" and not self.in_pre:
            self.fmt["code"] -= 1
        elif tag == "a":
            self.href = None
        elif tag in ("td", "th"):
            self.flush()
            if self.cell_header:
                self.fmt["bold"] -= 1
            self.table[-1].append((self.cell_header, "".join(self.cell)))
            self.cell = None
        elif tag == "table":
            self.body.append(self.table_xml(self.table))
            self.table = None

    def handle_data(self, data):
        if self.in_pre:
            self.pre_text.append(data)
            return
        if self.block is None:
            if not data.strip():
                return
            self.start_para("Normal")
        if not self.runs and self.block != "Code":
            data = data.lstrip("\n")
        data = data.replace("\n", " ")
        run = run_xml(data, bold=self.fmt["bold"] > 0, italic=self.fmt["italic"] > 0,
                      code=self.fmt["code"] > 0, link=self.href is not None)
        if self.href:
            self.links.append(self.href)
            rid = f"rIdLink{len(self.links)}"
            run = f'<w:hyperlink r:id="{rid}">{run}</w:hyperlink>'
        self.runs.append(run)

    def table_xml(self, rows):
        ncols = max(len(r) for r in rows)
        widths = {3: [3150, 2050, 4880], 2: [3200, 6880]}.get(ncols, [10080 // ncols] * ncols)
        if ncols == 3 and rows and rows[0] and rows[0][0][1].count("#"):
            widths = [500, 2700, 6880]                   # "# | Issue | Why it matters"
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        out = []
        for row in rows:
            cells = []
            for j, (is_header, content) in enumerate(row):
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="EEF3FF"/>' if is_header else ""
                cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{widths[j]}" w:type="dxa"/>{shade}</w:tcPr>'
                             f'{content or "<w:p/>"}</w:tc>')
            header = "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if row and row[0][0] else "<w:trPr><w:cantSplit/></w:trPr>"
            out.append(f"<w:tr>{header}{''.join(cells)}</w:tr>")
        borders = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="D0D5DD"/>'
                          for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        return (f'<w:tbl><w:tblPr><w:tblW w:w="10080" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
                f'<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>'
                f'<w:bottom w:w="40" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr>'
                f'<w:tblGrid>{grid}</w:tblGrid>{"".join(out)}</w:tbl><w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')


STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:docDefaults>
  <w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="21"/><w:color w:val="1A1A1A"/></w:rPr></w:rPrDefault>
  <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault>
 </w:docDefaults>
 <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
 <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:after="160"/></w:pPr><w:rPr><w:b/><w:color w:val="{BLUE}"/><w:sz w:val="36"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:keepNext/><w:pBdr><w:bottom w:val="single" w:sz="12" w:space="2" w:color="{BLUE}"/></w:pBdr>
   <w:spacing w:before="320" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr>
  <w:rPr><w:b/><w:color w:val="{BLUE}"/><w:sz w:val="28"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="ListText"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:after="60"/></w:pPr></w:style>
 <w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="19"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:ind w:left="120" w:right="120"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="{MONO}" w:hAnsi="{MONO}" w:cs="{MONO}"/><w:sz w:val="16"/></w:rPr></w:style>
</w:styles>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
 <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
 <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
 <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Coinbase SEA: Insights to Impact Challenge</dc:title>
 <dc:creator>Armaan Singla</dc:creator></cp:coreProperties>"""


def numbering_xml(kinds):
    """Native Word/Docs lists: abstractNum 1 = bullets, 2 = decimal; one w:num per list, restarting at 1."""
    def levels(fmt):
        out = []
        for lvl in range(3):
            text = "•" if fmt == "bullet" else f"%{lvl + 1}."
            font = '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr>' if fmt == "bullet" else ""
            out.append(f'<w:lvl w:ilvl="{lvl}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
                       f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
                       f'<w:pPr><w:ind w:left="{720 + 360 * lvl}" w:hanging="360"/></w:pPr>{font}</w:lvl>')
        return "".join(out)
    abstract = (f'<w:abstractNum w:abstractNumId="1">{levels("bullet")}</w:abstractNum>'
                f'<w:abstractNum w:abstractNumId="2">{levels("decimal")}</w:abstractNum>')
    nums = "".join(f'<w:num w:numId="{i}"><w:abstractNumId w:val="{1 if k == "ul" else 2}"/>'
                   f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
                   for i, k in enumerate(kinds, 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{abstract}{nums}</w:numbering>')


def build(md_path, docx_path):
    html = markdown.markdown(open(md_path, encoding="utf-8").read(), extensions=["tables", "fenced_code"])
    b = DocxBuilder()
    b.feed(html)
    b.close()
    b.flush()
    sect = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="1008" w:right="1080" w:bottom="1008" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<w:body>{"".join(b.body)}{sect}</w:body></w:document>')
    rels = "".join(f'<Relationship Id="rIdLink{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   f'relationships/hyperlink" Target="{escape(h)}" TargetMode="External"/>'
                   for i, h in enumerate(b.links, 1))
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships/numbering" Target="numbering.xml"/>{rels}</Relationships>')
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("docProps/core.xml", CORE)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/numbering.xml", numbering_xml(b.list_kinds))
        z.writestr("word/_rels/document.xml.rels", doc_rels)


if __name__ == "__main__":
    build(*(sys.argv[1:3] if len(sys.argv) > 2 else ("submission.md", "submission.docx")))
    print(f"wrote {sys.argv[2] if len(sys.argv) > 2 else 'submission.docx'}")
