"""Render saved research into a PDF without fetching data or invoking a model."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
import math
from pathlib import Path
import re
from threading import Lock
from urllib.parse import urlsplit

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


INK = colors.HexColor('#26334a')
MUTED = colors.HexColor('#737b8b')
ACCENT = colors.HexColor('#6b59b4')
_PDF_LOCK = Lock()


def _text(value):
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or ''))
    return text.translate(str.maketrans({'\u2011': '-', '\u2013': '-', '\u2014': '-', '\u2212': '-'}))


def _markup(value):
    text = escape(_text(value))
    return re.sub(r'[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+',
                  lambda match: '<font name="AetherSansSC">'+match[0]+'</font>', text).replace('\n', '<br/>')


def _url(value):
    text = str(value or '')
    try:
        parsed = urlsplit(text)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
            return text
    except ValueError:
        pass
    return ''


def _clock(value):
    try:
        instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if instant.utcoffset() is not None:
            return instant.astimezone(timezone.utc).strftime('%d %b %Y %H:%M UTC')
    except ValueError:
        pass
    return 'Unknown'


def _number(value):
    try:
        number = Decimal(str(value))
        return f'{number:,.2f}' if number.is_finite() else 'Unavailable'
    except InvalidOperation:
        return 'Unavailable'


def _chart(chart, width):
    """Draw saved values on their actual time axis, preserving declared gaps."""
    points = []
    for point in chart.get('points', []):
        try:
            instant = datetime.fromisoformat(point['at'].replace('Z', '+00:00')).timestamp()
            value = float(point['value'])
            if math.isfinite(value):
                points.append((instant, value, point.get('gap_before', False)))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    if len(points) < 2:
        return None
    start = datetime.fromisoformat(chart['start'].replace('Z', '+00:00')).timestamp()
    end = datetime.fromisoformat(chart['end'].replace('Z', '+00:00')).timestamp()
    if end <= start:
        return None
    low, high = min(point[1] for point in points), max(point[1] for point in points)
    padding = (high-low)*.08 or max(abs(high)*.02, .000001)
    low, high = low-padding, high+padding
    left, right, bottom, top = 62, width-10, 37, 178
    drawing = Drawing(width, 196)
    for index in range(5):
        value = low+(high-low)*index/4
        y = bottom+(top-bottom)*index/4
        drawing.add(Line(left, y, right, y, strokeColor=colors.HexColor('#e9e7f0'), strokeWidth=.5))
        magnitude = abs(value)
        divisor, suffix = (1e9, 'B') if magnitude >= 1e9 else (1e6, 'M') if magnitude >= 1e6 else (1, '')
        label = f'{value/divisor:,.2f}{suffix}' if magnitude >= .01 else f'{value:.6g}'
        drawing.add(String(left-8, y-3, label, fontName='Helvetica', fontSize=8, textAnchor='end', fillColor=MUTED))
    for fraction in (0, .5, 1):
        instant = start+(end-start)*fraction
        label = datetime.fromtimestamp(instant, timezone.utc).strftime('%d %b %H:%M')
        drawing.add(String(left+(right-left)*fraction, 19, label, fontName='Helvetica', fontSize=8,
                           textAnchor='start' if fraction == 0 else 'end' if fraction == 1 else 'middle', fillColor=MUTED))
    segment = []
    for instant, value, gap in points:
        if gap and segment:
            drawing.add(Circle(*segment, 2, fillColor=ACCENT, strokeColor=ACCENT) if len(segment)==2
                        else PolyLine(segment, strokeColor=ACCENT, strokeWidth=1.8))
            segment = []
        segment.extend([left+(instant-start)/(end-start)*(right-left), bottom+(value-low)/(high-low)*(top-bottom)])
    if segment:
        drawing.add(Circle(*segment, 2, fillColor=ACCENT, strokeColor=ACCENT) if len(segment)==2
                    else PolyLine(segment, strokeColor=ACCENT, strokeWidth=1.8))
    return drawing


def render_report_pdf(packet, report_id, *, evidence_lookup=None, notices=()):
    """Export the immutable report plus attributed notices and public source links."""
    # ReportLab's dynamic TrueType font must not be shared by concurrent builds.
    with _PDF_LOCK:
        return _render_report_pdf(packet,report_id,evidence_lookup=evidence_lookup,notices=notices)


def _render_report_pdf(packet, report_id, *, evidence_lookup, notices):
    if 'AetherSansSC' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('AetherSansSC',str(Path(__file__).with_name('fonts')/'AetherSansSC-Regular.ttf')))
    # Team-reader packets already contain only fields selected for sharing.
    shared = 'report' not in packet
    draft = packet.get('report', packet)
    presentation = packet.get('presentation') or {}
    overview = presentation.get('overview') or draft.get('scope') or {}
    styles = {
        'body': ParagraphStyle('body', fontName='Helvetica', fontSize=10.5, leading=16, textColor=INK, spaceAfter=9),
        'title': ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=23, leading=28, textColor=INK, spaceAfter=12),
        'h2': ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=14, leading=19, textColor=ACCENT, spaceBefore=18, spaceAfter=9, keepWithNext=True),
        'h3': ParagraphStyle('h3', fontName='Helvetica-Bold', fontSize=11, leading=16, textColor=INK, spaceBefore=9, spaceAfter=7, keepWithNext=True),
        'small': ParagraphStyle('small', fontName='Helvetica', fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=7),
    }
    story = []

    def paragraph(text, style='body', suffix=''):
        story.append(Paragraph(_markup(text)+suffix, styles[style]))

    def heading(text, level='h2'):
        paragraph(text, level)

    evidence_ids = []

    def refs(ids):
        links = []
        for rid in ids or []:
            if rid not in evidence_ids:
                evidence_ids.append(rid)
            number = evidence_ids.index(rid)+1
            links.append(f'<link href="#evidence-{number}" color="#6b59b4">[{number}]</link>')
        return ' '+ ' '.join(links) if links else ''

    def claims(rows):
        for claim in rows or []:
            paragraph(claim.get('statement', claim.get('text', claim.get('condition', ''))), suffix=refs(claim.get('evidence_ids')))

    def bullets(rows):
        for row in rows or []:
            paragraph('- '+str(row))

    paragraph('AETHER  /  RESEARCH REPORT', 'small')
    heading(draft.get('title', 'Research report'), 'title')
    paragraph('Published '+_clock(packet.get('created_at'))+'  |  '+report_id, 'small')
    if overview.get('start') and overview.get('end'):
        paragraph('Research window  '+_clock(overview['start'])+' to '+_clock(overview['end'])+' (end exclusive)', 'small')
    if overview.get('evidence_as_of'):
        paragraph('Evidence cutoff  '+_clock(overview['evidence_as_of']), 'small')
    status = 'Shared research' if shared else 'Sources checked' if packet.get('review', {}).get('status') == 'reviewed' else 'Archived research'
    paragraph(status+'. Source checks assess support in saved material. Publisher accuracy and future performance remain uncertain.', 'small')
    if notices:
        paragraph(f'{len(notices)} reader notice(s) are attached at the end of this report. Review them alongside the original text.', 'small')
    heading('Summary')
    paragraph(draft.get('summary', ''))

    charts = presentation.get('charts') or []
    if charts:
        heading('Key data')
    width = A4[0]-88
    for chart in charts:
        heading(f"{chart.get('asset', '')} {chart.get('title', '')}", 'h3')
        paragraph(f"{chart.get('provider', '')} | {chart.get('frequency', '')} | {chart.get('unit', '')} | UTC", 'small', refs([chart['evidence_id']]))
        drawing = _chart(chart, width)
        if drawing is not None:
            story.append(drawing)
        if chart.get('points'):
            first, last = chart['points'][0], chart['points'][-1]
            paragraph('First point '+_number(first['value'])+'; last point '+_number(last['value'])+' '+chart.get('unit', '')+'.', 'small')
        coverage = chart.get('coverage', {})
        if coverage.get('internal_gap_count') or coverage.get('starts_after_window') or coverage.get('ends_before_window'):
            paragraph('Coverage is incomplete within the research window. Lines are interrupted across saved gaps.', 'small')

    if draft.get('key_changes'):
        heading('What matters')
        claims(draft['key_changes'])
    if shared:
        heading('Findings and evidence')
        claims(draft.get('statements'))
    else:
        heading('Facts and interpretation')
        claims(draft.get('observations'))
        if draft.get('interpretations'):
            heading('Interpretation', 'h3')
            claims(draft['interpretations'])
        if draft.get('judgment_reason'):
            heading('Research judgment', 'h3')
            paragraph(draft['judgment_reason'])
    for document in presentation.get('documents') or []:
        heading(document['title'])
        role = {'primary': 'Main in-window evidence', 'background': 'Earlier background', 'later_update': 'Update after this window',
                'correction': 'Correction', 'counterevidence': 'Counterevidence'}.get(document.get('role'), 'Saved original')
        paragraph(document.get('publisher', '')+' | '+role, 'small', refs([document['evidence_id']]))
        paragraph(document.get('relevance', ''))
        paragraph('Quoted passage: '+document.get('quote', ''))
        paragraph('Published '+_clock(document.get('published_at'))+'; captured '+_clock(document.get('retrieved_at')), 'small')
    for explanation in draft.get('explanations') or []:
        heading(explanation['name'])
        paragraph('Horizon: '+explanation['horizon'], 'small')
        for label, rows in [('Observed basis', explanation['observed_basis']), ('Hypothesis', [explanation['hypothesis']]),
                            ('Applicability', explanation['applicability']), ('Alternative explanations', explanation.get('alternative_explanations')),
                            ('Counterevidence', explanation.get('counterevidence')), ('Invalidation', explanation['invalidation'])]:
            if rows:
                heading(label, 'h3')
                claims(rows)
        bullets(explanation['uncertainties'])
    for forecast in draft.get('forecasts') or []:
        heading('Conditional outlook')
        paragraph(forecast['statement'], suffix=refs(forecast['supporting_evidence_ids']+forecast.get('counter_evidence_ids', [])))
        paragraph('Horizon: '+str(forecast['horizon_days'])+' days. '+forecast['uncertainty'], 'small')
        bullets(forecast.get('assumptions'))
    purchase = draft.get('purchase_decision')
    if purchase:
        heading('Decision context')
        paragraph('Horizon: '+str(purchase['horizon_days'])+' days.', 'small')
        for label, key in [('Conditions to investigate', 'conditions_to_consider'), ('Reasons to wait', 'reasons_to_wait')]:
            heading(label, 'h3')
            claims(purchase[key])
        for label, key in [('Invalidation', 'invalidation_conditions'), ('Missing information', 'missing_information'),
                           ('Monitor', 'monitor'), ('Costs and execution assumptions', 'cost_and_execution_notes')]:
            heading(label, 'h3')
            bullets(purchase[key])
        refs(purchase.get('simulation_evidence_ids'))
    advice = draft.get('advice')
    if advice:
        heading('Conditional research suggestion')
        paragraph(advice['summary'], suffix=refs(advice.get('context_evidence_ids')))
        for label, key in [('Reasoning', 'rationale'), ('Conditions', 'conditions'), ('Counterpoints', 'counterpoints'), ('Invalidation', 'invalidation')]:
            if advice.get(key):
                heading(label, 'h3')
                claims(advice[key])
        for case in advice.get('historical_cases') or []:
            heading('Historical comparison - '+case['name'], 'h3')
            paragraph('Case ends '+_clock(case['case_end'])+' | '+case['availability'].replace('_', ' '), 'small', refs(case['evidence_ids']))
            for label, key in [('Similarities', 'similarities'), ('Differences', 'differences'), ('Limits of transfer', 'limitations')]:
                paragraph(label, 'small')
                bullets(case[key])
        for label, key in [('Assumptions', 'assumptions'), ('Missing information', 'missing_information'), ('Review triggers', 'review_triggers')]:
            if advice.get(key):
                heading(label, 'h3')
                bullets(advice[key])
    if draft.get('questions_to_watch'):
        heading('What to watch next')
        bullets(draft['questions_to_watch'])
    heading('Limits to keep in mind')
    bullets(draft.get('limitations'))

    if notices:
        heading('Reader notices')
    for notice in notices:
        paragraph(notice.get('note', ''))
        review = notice.get('latest_review') or {}
        paragraph('Attributed review: '+review.get('assessment', 'Awaiting review').replace('_', ' '), 'small')
        if review.get('correction'):
            paragraph(review['correction'], suffix=refs(review.get('evidence_ids')))
        resolution = notice.get('resolution')
        if resolution:
            paragraph('An updated report is available: '+resolution['replacement_report_id'], 'small')

    heading('Evidence references')
    source_urls = []
    for index, rid in enumerate(evidence_ids, 1):
        row = {}
        if evidence_lookup:
            try:
                row = evidence_lookup(rid)
            except (KeyError, ValueError, FileNotFoundError):
                pass
        source = row.get('source') or {}
        title = row.get('title') or source.get('name') or row.get('dataset_id') or 'Saved evidence'
        story.append(Paragraph(f'<a name="evidence-{index}"/>[{index}] '+_markup(title), styles['h3']))
        paragraph(rid, 'small')
        url = _url(row.get('url') or source.get('url') or packet.get('evidence', {}).get(rid))
        if url:
            source_urls.append(url)
            story.append(Paragraph('<link href="'+escape(url, quote=True)+'" color="#6b59b4">'+_markup(url)+'</link>', styles['small']))
        else:
            paragraph('Saved record or calculation. Publisher input sources are listed below.', 'small')
    publishers = packet.get('sources') or []
    for item in [*publishers, *(source for chart in charts for source in chart.get('sources', [])), *(document.get('url') for document in presentation.get('documents', []))]:
        url = _url(item if isinstance(item, str) else item.get('url'))
        if url and url not in source_urls:
            source_urls.append(url)
            paragraph('Publisher source', 'h3')
            story.append(Paragraph('<link href="'+escape(url, quote=True)+'" color="#6b59b4">'+_markup(url)+'</link>', styles['small']))
    story.append(Spacer(1, 8))
    if overview.get('user_prompt'):
        heading('Technical provenance')
        paragraph('Original task question: '+overview['user_prompt'], 'small')
        paragraph("A captured media publication remains that publisher's account; main in-window evidence does not imply an issuer's first-hand statement.", 'small')
    paragraph('Aether research support. Review the evidence and make your own investment decision.', 'small')

    def page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#e6e3ef'))
        canvas.line(44, 40, A4[0]-44, 40)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(44, 27, 'AETHER  /  '+report_id)
        canvas.drawRightString(A4[0]-44, 27, str(doc.page))
        canvas.restoreState()

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=44, leftMargin=44, topMargin=40, bottomMargin=55,
                                 title=_text(draft.get('title', 'Research report')), author='Aether', pageCompression=1)
    document.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()
