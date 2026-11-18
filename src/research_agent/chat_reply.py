"""Local replies for explicit chat controls and short conversational turns."""

import re

from .research_policy import has_recent_report_period

_STATUS = re.compile(
    r'(?:所以\s*)?(?:你\s*)?(?:是\s*不是\s*|是\s*)?(?:这[份次篇]\s*|刚才的\s*)?'
    r'(?:已经\s*)?(?:(?:报告|周报|任务)\s*(?:是\s*)?)?(?:已经\s*)?'
    r'(?:(?:生成\s*完(?:毕|成)?|生成好|写完|写好|做完|跑完|结束|完成|发布|出结果|出来|好)'
    r'(?:了(?:吗|没|没有)?|吗|没|没有)|还在(?:跑|生成|处理)(?:吗)?)|'
    r'(?:现在\s*)?(?:进度(?:怎么样|如何|到哪(?:一?步|里)?(?:了)?)|跑到哪(?:一)?步了|报告在哪(?:里|儿)?(?:看)?|还要多久)|'
    r'(?:so\s+)?(?:is|are|did|have)\s+(?:(?:the|you|it|your|this|that|report|research|job)\s+)*'
    r'(?:done|finished|finish|completed|complete|still running|ready|published)|'
    r'what(?: is|\x27s) (?:the |its )?(?:status|progress)|where(?: is|\x27s) (?:the |my )?report'
)
_REPORT = re.compile(r'报告|周报|\breport\b')
_NOTICE_QUESTION = re.compile(
    r'(?:(?:这个|这份|这篇|刚才(?:的|那份)?)\s*(?:报告|周报|说明)|证据缺口说明)'
    r'\s*(?:是)?\s*(?:什么|啥)\s*意思|'
    r'(?:为什么|怎么)\s*(?:报告|周报)\s*(?:没(?:有)?(?:生成|发布)|失败)(?:了)?|'
    r'what (?:does (?:this|the) (?:report|notice) mean|is (?:this|the) evidence (?:gap|availability) notice)|'
    r'why (?:did (?:my|the) report fail|was (?:my|the) report not published)'
)
_COURTESY = {
    'greeting': r'你好[呀啊]?|您好|嗨|哈喽|在吗|hi|hey|hello',
    'thanks': r'谢谢(?:你|了)?|多谢|感谢(?:你|了)?|辛苦了|thanks|thank you',
    'help': r'你(?:能|可以)做什么|你是谁|怎么用(?:这个)?(?:助手|网站|aether)|what can you do|who are you|how do i use aether',
}
_REPORT_REQUEST = re.compile(
    r'(?:^|[，,。；;]\s*|(?:并且?|然后)\s*)'
    r'(?:(?:请(?:你)?|能(?:不能|否)|可以|麻烦(?:你)?|帮我|给我|我想让你)\s*)*'
    r'(?:生成|写|做|制作|输出|起草|出一[份篇])[^\n。！？!?；;]{0,36}(?:报告|[周月简]报)|'
    r'^\s*(?:(?:please|(?:can|could|would) you|i (?:want|need) you to)\s+)*'
    r'(?:give me\s+(?:an?\s+)?(?:(?:weekly|daily|monthly|research)\s+)?|'
    r'(?:write|generate|create|prepare|produce|draft)\s+'
    r'(?![^.!?\n]{0,35}\b(?:summary|example|template|instructions)\s+(?:of|for)\b)[^.!?\n]{0,45})'
    r'\b(?:report|brief)\b', re.I
)
_NO_REPORT = re.compile(
    r'(?:不(?:要|用)|无需|别)\s*(?:再)?\s*(?:生成|写|做|制作|发布|出)\s*(?:一[份篇]|新)?\s*(?:报告|[周月简]报)|'
    r'\b(?:do not|don\x27t|no need to)\s+(?:write|generate|create|prepare|produce|draft|publish)\b[^.!?\n]{0,45}\b(?:report|brief)\b', re.I
)
_CHINESE_BARE_REPORT_REQUEST = re.compile(
    r'^\s*(?:请\s*)?(?:给我|提供|来)\s*'
    r'(?P<scope>[^。！？!?；;，,\n]{1,48}?)\s*(?:报告|[周月简]报)'
    r'\s*(?:吧|一下)?\s*(?=$|[。！？!?；;，,\n])'
)
_EXISTING_REPORT_HINT = re.compile(r'^\s*(?:看|找|查|打开|下载)|之前|上次|上回|刚才|现有|已有|已发布|那[份篇]|模板|样例|示例|摘要')


def _normalized_prompt(prompt):
    return re.sub(r'\s+', ' ', prompt.strip().casefold()).rstrip('?!。！？~～ ').strip()


def asks_about_report_notice(prompt):
    return bool(_NOTICE_QUESTION.fullmatch(_normalized_prompt(prompt)))


def chat_intent(prompt):
    text = _normalized_prompt(prompt)
    if not text or len(text) > 80:
        return None
    if _STATUS.fullmatch(text):
        return 'report_status' if _REPORT.search(text) else 'status'
    return next((kind for kind, pattern in _COURTESY.items() if re.fullmatch(pattern, text)), None)


def requests_report(prompt):
    """Recognize explicit authoring requests before selecting the chat workflow."""
    if chat_intent(prompt) or _NO_REPORT.search(prompt):
        return False
    text = re.sub(r'```[\s\S]*?```|`[^`]*`|“[^”]*”|「[^」]*」|"[^"\n]*"', ' ', prompt).strip()
    if _REPORT_REQUEST.search(text):
        return True
    match = _CHINESE_BARE_REPORT_REQUEST.search(text)
    if not match or _EXISTING_REPORT_HINT.search(match['scope']):
        return False
    scope = match['scope']
    return bool(re.match(r'(?:一|1)\s*[份篇]|新',scope) or has_recent_report_period(scope))


def research_job(jobs, active_id=None):
    """Select the active job or the latest research turn, excluding local replies."""
    active = next((job for job in reversed(jobs) if job['job_id'] == active_id), None)
    return active or next((job for job in reversed(jobs) if not job.get('chat_reply')), None)


def chat_answer(intent, prompt, jobs, active_id):
    chinese = bool(re.search(r'[\u3400-\u9fff]', prompt))
    conversational = {
        'greeting': ('你好。你想了解什么？', 'Hi. What would you like to know?'),
        'thanks': ('不客气。', 'You’re welcome.'),
        'help': ('我是 Aether。你可以直接问我研究问题、让我生成报告，或查看当前任务的进度。已发布的报告在 Analyst → Reports。',
                 'I’m Aether. Ask a research question, request a report, or check the progress of your task. Published reports are under Analyst → Reports.'),
    }
    if intent in conversational:
        return conversational[intent][0 if chinese else 1]
    research = [job for job in jobs if not job.get('chat_reply') and job.get('request_purpose') != 'market_refresh'
                and (job.get('published_reports') or not chat_intent(job.get('prompt', '')))]
    if intent in ('report_status', 'report_explanation'):
        research = [job for job in research if job.get('published_reports') or
                    job.get('deliverable') == 'report' or requests_report(job.get('prompt', ''))]
    job = research_job(research, active_id)
    if intent == 'report_explanation':
        if not job or job.get('report_delivery_kind') != 'evidence_gap' or not job.get('published_reports'):
            return None
        return ('上一轮保存的是证据缺口说明。任务结束时还没有草稿通过发布检查，'
                '所以这份说明没有周报研究结论。' if chinese else
                'The last attempt saved an evidence availability notice. No draft passed the publication '
                'checks before the task ended, so the notice contains no weekly research findings.')
    if not job:
        return ('这个会话还没有报告任务。' if intent == 'report_status' else '这个会话还没有研究任务。') if chinese else (
            'There is no report task in this chat yet.' if intent == 'report_status' else 'There is no research task in this chat yet.')
    if job.get('published_reports'):
        if job.get('report_delivery_kind') == 'evidence_gap':
            return ('只保存了证据缺口说明，这一轮没有发布经核验的研究结论。' if chinese else
                    'Only an evidence availability notice was saved. This request did not produce verified research findings.')
        return ('报告已发布，可以在 Analyst → Reports 查看。' if chinese else
                'The report was published. You can open it under Analyst → Reports.')
    answers = {
        'running': ('任务仍在运行，还没有发布报告。', 'The task is still running. No report has been published yet.'),
        'waiting_for_conversation': ('任务正在等回复，完成后才能继续。', 'The task is waiting for a reply before it can continue.'),
        'completed': ('上一轮的回答已经完成，保存在这个聊天里。这一轮没有发布报告。',
                      'The previous answer is ready in this chat. That turn did not publish a report.'),
        'failed': ('上一轮任务未能完成，没有发布报告。', 'The previous task did not complete or publish a report.'),
        'cancelled': ('上一轮任务已停止，没有发布报告。', 'The previous task was stopped before publishing a report.'),
        'interrupted': ('上一轮任务已中断，没有发布报告。', 'The previous task was interrupted before publishing a report.'),
    }
    return answers[job['state']][0 if chinese else 1]
