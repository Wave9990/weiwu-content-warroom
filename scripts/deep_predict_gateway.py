#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_PATH = PROJECT_ROOT / 'docs' / 'data' / 'weiwu-account.json'
RUBRIC_PATH = PROJECT_ROOT / 'rubric_notes.md'
HERMES_BIN = '/Users/wave/.hermes/hermes-agent/venv/bin/hermes'


def load_context() -> dict[str, Any]:
    account = json.loads(ACCOUNT_PATH.read_text(encoding='utf-8'))
    rubric = RUBRIC_PATH.read_text(encoding='utf-8') if RUBRIC_PATH.exists() else ''
    works = sorted(account.get('works', []), key=lambda item: item.get('play_count') or 0, reverse=True)[:8]
    anchors = [
        {
            'title': item.get('title') or item.get('theme') or '',
            'category': item.get('theme_category'),
            'angle': item.get('content_angle'),
            'views': item.get('play_count'),
            'likes': item.get('digg_count'),
            'comments': item.get('comment_count'),
            'saves': item.get('collect_count'),
            'shares': item.get('share_count'),
        }
        for item in works
    ]
    return {
        'account': account.get('account', {}),
        'metrics': account.get('metrics', {}),
        'positioning': account.get('content_positioning', {}),
        'anchors': anchors,
        'rubric': rubric[:4000],
    }


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError('model output did not contain JSON')
    return json.loads(match.group(0))


def build_prompt(draft: str) -> str:
    context = load_context()
    return f'''你是唯吾内容作战台的“模型深度预测”后端。请基于账号历史样本和 rubric，对一条尚未发布的新稿做发布前预测。

强规则：
1. 这是发布前深度分析，不是正式锁定 blind prediction；不要声称已经写入 predictions 文件。
2. 历史作品只能作为 historical anchors，不得伪装成盲预测样本。
3. 当前阶段只预测基础数据：播放、点赞、评论、收藏、分享；不要预测私信、咨询、加微、成交。
4. Rubric 里的 Lead 在本项目中只理解为“目标人群匹配度/是否吸引装修老板和设计师”，不要写成咨询数、私信数或成交概率。
5. 输出必须是合法 JSON，不要 Markdown，不要代码块，不要额外解释。

账号上下文：
{json.dumps(context, ensure_ascii=False)}

新稿：
{draft}

请输出 JSON，字段必须完整：
{{
  "verdict": "拍/改完再拍/暂缓",
  "confidence": "低/中/高",
  "one_line_reason": "一句话说明",
  "predicted_metrics": {{"views_low": 0, "views_mid": 0, "views_high": 0, "likes_mid": 0, "comments_mid": 0, "saves_mid": 0, "shares_mid": 0}},
  "rubric_scores": [{{"name": "Pain", "score": 0, "reason": ""}}, {{"name": "Proof", "score": 0, "reason": ""}}, {{"name": "Immersion", "score": 0, "reason": ""}}, {{"name": "Method", "score": 0, "reason": ""}}, {{"name": "Lead", "score": 0, "reason": ""}}, {{"name": "Novelty", "score": 0, "reason": ""}}, {{"name": "Clarity", "score": 0, "reason": ""}}],
  "matched_anchors": [{{"title": "", "why_similar": "", "views": 0, "transferable_pattern": ""}}],
  "strengths": [""],
  "risks": [""],
  "specific_rewrites": [{{"part": "开头/中段/结尾/标题", "problem": "", "rewrite": ""}}],
  "shooting_advice": [""],
  "decision_rule": "什么条件下值得最终拍出来发布"
}}
'''


def run_model(draft: str, timeout: int) -> dict[str, Any]:
    prompt = build_prompt(draft)
    try:
        proc = subprocess.run(
            [
                HERMES_BIN,
                'chat',
                '--query',
                prompt,
                '--quiet',
                '--source',
                'tool',
                '--provider',
                'xiaomi',
                '--model',
                'mimo-v2.5-pro',
                '--skills',
                'cheat-on-content',
                '--max-turns',
                '1',
            ],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or 'Hermes model call failed').strip()[-2000:])
        result = extract_json(proc.stdout)
        result['engine'] = 'hermes_model_gateway'
        return result
    except Exception as exc:
        fallback = local_structured_prediction(draft)
        fallback['engine'] = 'local_structured_fallback'
        fallback['model_error'] = str(exc)[-500:]
        return fallback


def local_structured_prediction(draft: str) -> dict[str, Any]:
    context = load_context()
    anchors = context['anchors']
    keywords = ['开工', '成品', '案例', '工地', '老板', '设计师', '业主', '同行', '信任', '亮点', '无脚本', '第一视角', '拍摄', '痛点', '方法', '服务', '风险', '价格']

    def hits(text: str) -> set[str]:
        return {keyword for keyword in keywords if keyword in text}

    draft_hits = hits(draft)
    ranked = []
    for anchor in anchors:
        title = anchor.get('title') or ''
        anchor_hits = hits(title)
        overlap = len(draft_hits & anchor_hits)
        ranked.append((overlap, anchor))
    ranked.sort(key=lambda item: (item[0], item[1].get('views') or 0), reverse=True)
    matched = [item[1] for item in ranked[:3] if item[0] > 0] or anchors[:2]

    has_pain = any(word in draft for word in ['痛点', '问题', '最怕', '风险', '比价格', '广告位'])
    has_proof = any(word in draft for word in ['案例', '真实', '数据', '现场', '工地', '业主'])
    has_immersion = any(word in draft for word in ['第一视角', '现场', '跟拍', '无脚本', '带你'])
    has_method = any(word in draft for word in ['方法', '步骤', '判断', '框架', '怎么'])
    has_target = any(word in draft for word in ['装修公司', '老板', '设计师', '家装'])
    has_novelty = any(word in draft for word in ['不是', '别再', '真正', '最大的问题', '反常识'])
    clarity = len(re.findall(r'[。！？；]', draft)) >= 3 or ('先' in draft and '再' in draft)

    rubric = [
        ('Pain', 4 if has_pain else 2, '痛点是否具体击中装修老板/设计师。'),
        ('Proof', 4 if has_proof else 1, '是否有真实案例、现场或数据支撑。'),
        ('Immersion', 4 if has_immersion else 1, '是否有第一视角和现场感。'),
        ('Method', 4 if has_method else 2, '是否给出可照着改的判断方法。'),
        ('Lead', 4 if has_target else 2, '是否明确吸引装修老板/设计师这类目标人群。'),
        ('Novelty', 4 if has_novelty else 2, '是否区别于同行常规表达。'),
        ('Clarity', 4 if clarity else 2, '结构是否从问题到原因再到做法。'),
    ]
    score_total = sum(item[1] for item in rubric)
    base = max([anchor.get('views') or 0 for anchor in matched] + [context['metrics'].get('avg_play_count') or 2000])
    multiplier = 0.45 + score_total / 50
    mid = round(base * multiplier)
    low = max(500, round(mid * 0.45))
    high = max(1200, round(mid * 1.75))
    verdict = '拍' if score_total >= 25 and has_proof and has_method else ('改完再拍' if score_total >= 19 else '暂缓')
    return {
        'verdict': verdict,
        'confidence': '中' if len(anchors) >= 8 else '低',
        'one_line_reason': '这条方向有目标人群和痛点，但是否值得拍，关键看能否补真实案例、现场感和可执行判断方法。',
        'predicted_metrics': {
            'views_low': low,
            'views_mid': mid,
            'views_high': high,
            'likes_mid': max(10, round(mid * 0.025)),
            'comments_mid': max(0, round(mid * 0.0006)),
            'saves_mid': max(5, round(mid * 0.018)),
            'shares_mid': max(3, round(mid * 0.007)),
        },
        'rubric_scores': [{'name': name, 'score': score, 'reason': reason} for name, score, reason in rubric],
        'matched_anchors': [
            {
                'title': anchor.get('title') or '未命名作品',
                'why_similar': '关键词和内容方向相近，可作为 historical anchor 参考。',
                'views': anchor.get('views') or 0,
                'transferable_pattern': '参考它的“具体场景 + 方法拆解 + 可执行动作”，不要只停留在观点判断。',
            }
            for anchor in matched
        ],
        'strengths': [
            '方向能对准装修老板/设计师，不是泛泛的装修知识。',
            '如果保留“错误做法 vs 正确做法”的冲突，开头有机会抓住目标人群。',
        ],
        'risks': [
            '如果没有真实案例或现场画面，容易变成正确但不够有记忆点的观点。',
            '如果结尾没有具体判断方法，用户看完只会点头，不知道怎么照着改。',
        ],
        'specific_rewrites': [
            {
                'part': '开头',
                'problem': '现在可以更尖锐地制造冲突。',
                'rewrite': '装修公司老板做个人 IP，最怕的不是不会拍，而是一开口就像广告。你越讲材料、工艺、优惠，客户越拿你去比价。',
            },
            {
                'part': '中段',
                'problem': '需要补一个真实案例或场景。',
                'rewrite': '比如同样拍工地，不要先讲我们工艺多好，而是先讲业主最怕漏水、增项、延期，再用一个真实工地说明你怎么提前排掉风险。',
            },
            {
                'part': '结尾',
                'problem': '需要给判断方法。',
                'rewrite': '以后判断一条内容该不该拍，就问三个问题：客户有没有这个担心？我有没有真实案例证明？看完后他能不能判断我比别人更靠谱？',
            },
        ],
        'shooting_advice': [
            '建议用口播 + 工地/案例画面切片，不要纯坐着讲概念。',
            '字幕重点打出“广告位”“比价格”“装修风险”“真实案例”“判断方法”。',
            '视频节奏控制在：错误做法 3 秒，后果 5 秒，正确框架 15 秒，判断方法 10 秒。',
        ],
        'decision_rule': '如果能补进一个真实装修案例或陪跑账号案例，并把结尾改成可执行判断方法，就值得进入正式拍摄；否则先暂缓。',
    }


class Handler(BaseHTTPRequestHandler):
    timeout_seconds = 120

    def end_headers(self) -> None:
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == '/health':
            self.write_json({'ok': True, 'service': 'weiwu-deep-predict-gateway'})
        else:
            self.write_json({'ok': False, 'error': 'not found'}, status=404)

    def do_POST(self) -> None:
        if self.path != '/predict':
            self.write_json({'ok': False, 'error': 'not found'}, status=404)
            return
        try:
            length = int(self.headers.get('Content-Length') or '0')
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            draft = (payload.get('draft') or '').strip()
            if len(draft) < 20:
                self.write_json({'ok': False, 'error': '文稿太短，至少粘贴一段完整终稿。'}, status=400)
                return
            result = run_model(draft, self.timeout_seconds)
            self.write_json({'ok': True, 'result': result})
        except subprocess.TimeoutExpired:
            self.write_json({'ok': False, 'error': '模型深度预测超时，请稍后重试。'}, status=504)
        except Exception as exc:
            self.write_json({'ok': False, 'error': str(exc)}, status=500)

    def write_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f'[gateway] {self.address_string()} - {format % args}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Local Weiwu deep prediction gateway.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--timeout', type=int, default=45)
    args = parser.parse_args()
    Handler.timeout_seconds = args.timeout
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Weiwu deep prediction gateway listening on http://{args.host}:{args.port}')
    server.serve_forever()


if __name__ == '__main__':
    main()
