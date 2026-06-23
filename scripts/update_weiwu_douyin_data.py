#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITOR_PROJECT = Path('/Users/wave/dashboard/projects/douyin_account_monitoring')
MONITOR_SCRIPTS = MONITOR_PROJECT / 'scripts'
sys.path.insert(0, str(MONITOR_SCRIPTS))

from monitor import (  # type: ignore
    Account,
    build_f2_kwargs,
    collect_account,
    get_chrome_cookie_header,
    load_cache,
    result_to_dict,
    save_cache,
)

SEC_UID = 'MS4wLjABAAAAMc4utP7mxY1rnhc5n4zgAM21Et5poBPt-sVKZaKjd5YbryOqrnSfn-mkNmdL6BR1'
HOMEPAGE = 'https://v.douyin.com/XvggnHt3svA/'
CACHE = MONITOR_PROJECT / 'cache' / 'sec_uid_cache.json'
RAW_OUT = PROJECT_ROOT / 'data' / 'raw' / 'weiwu-full-f2-latest.json'
ACCOUNT_OUT = PROJECT_ROOT / 'docs' / 'data' / 'weiwu-account.json'
SAMPLES_OUT = PROJECT_ROOT / 'docs' / 'data' / 'weiwu-history-samples.json'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Update Weiwu Douyin account data via local F2 crawler.')
    parser.add_argument('--start', default='2020-01-01')
    parser.add_argument('--end', default=date.today().isoformat())
    parser.add_argument('--page-count', type=int, default=10)
    parser.add_argument('--max-pages', type=int, default=50)
    parser.add_argument('--timeout', type=int, default=12)
    parser.add_argument('--max-retries', type=int, default=3)
    return parser.parse_args()


def parse_day(value: str) -> date:
    return datetime.strptime(value, '%Y-%m-%d').date()


def classify(text: str) -> str:
    if any(k in text for k in ['开工', '工地', '服务日', '施工', '节点', '巡查']):
        return '工地/开工服务'
    if any(k in text for k in ['成品', '案例', '亮点', '房子', '户型', '设计']):
        return '案例/设计拆解'
    if any(k in text for k in ['老板', '装修公司', '装企', '营销', '短视频', '个人IP', 'IP', '陪跑', '账号']):
        return '装企IP运营'
    if any(k.lower() in text.lower() for k in ['gpt', 'ai', 'chatgpt']):
        return 'AI/工具人设'
    return '其他'


def content_angle(text: str) -> str:
    if '第一视角' in text or '无脚本' in text:
        return '第一视角现场教学'
    if '怎么' in text or '到底' in text or '问题' in text:
        return '问题拆解/方法论'
    if '老板' in text or '装企' in text:
        return '老板经营痛点'
    return '主题表达'


def as_sample(work: dict[str, Any]) -> dict[str, Any]:
    title = work.get('theme') or work.get('desc') or ''
    text = f'{work.get("desc") or ""}{title}'
    return {
        'id': f"historical-{work.get('aweme_id')}",
        'type': 'historical_anchor_not_blind_prediction',
        'platform': 'douyin',
        'aweme_id': work.get('aweme_id'),
        'title': title,
        'url': work.get('url'),
        'published_at': (work.get('create_time') or '')[:10],
        'created_time': work.get('create_time'),
        'theme_category': classify(text),
        'content_angle': content_angle(text),
        'metrics': {
            'views': int(work.get('play_count') or 0),
            'likes': int(work.get('digg_count') or 0),
            'comments': int(work.get('comment_count') or 0),
            'saves': int(work.get('collect_count') or 0),
            'shares': int(work.get('share_count') or 0),
            'engagement_score': int(work.get('engagement_score') or 0),
            'leads_or_inquiries': None,
            'conversion_status': 'unknown',
        },
        'transcript_status': 'not_fetched',
        'source': 'f2_douyin_with_chrome_cookie',
        'notes': '历史已发布作品，只作为 anchor；不能补写 blind prediction。',
    }


def enriched_work(sample: dict[str, Any]) -> dict[str, Any]:
    metrics = sample['metrics']
    return {
        **sample,
        'play_count': metrics['views'],
        'digg_count': metrics['likes'],
        'comment_count': metrics['comments'],
        'collect_count': metrics['saves'],
        'share_count': metrics['shares'],
        'desc': sample['title'],
        'theme': sample['title'],
        'create_time': sample['created_time'],
    }


def write_outputs(raw_payload: dict[str, Any]) -> dict[str, Any]:
    result = raw_payload['result']
    profile = result.get('profile') or {}
    works = result.get('works') or []
    samples = sorted([as_sample(work) for work in works], key=lambda item: item['created_time'] or '', reverse=True)
    metrics = [item['metrics'] for item in samples]
    aweme_count = int(profile.get('aweme_count') or 0)
    sample_summary = {
        'project': 'weiwu-content-warroom',
        'account': '唯吾',
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'sample_policy': 'historical anchors only; not blind predictions; safe for T+3 reference',
        'data_source': {
            'primary': 'F2 Douyin crawler with local Chrome login cookie',
            'raw_file': str(RAW_OUT),
            'redfox_check': '专用 key 可鉴权；queryUser data=null；queryWorkList total=0；queryWork data=null。本轮不作为唯吾数据源。',
        },
        'account_snapshot': {
            'nickname': profile.get('nickname'),
            'unique_id': profile.get('unique_id'),
            'follower_count': profile.get('follower_count'),
            'following_count': profile.get('following_count'),
            'aweme_count': aweme_count,
            'total_favorited': profile.get('total_favorited'),
            'captured_works': len(samples),
            'missing_works_count': max(0, aweme_count - len(samples)),
        },
        'performance_summary': {
            'total_views': sum(item['views'] for item in metrics),
            'total_likes': sum(item['likes'] for item in metrics),
            'total_comments': sum(item['comments'] for item in metrics),
            'total_saves': sum(item['saves'] for item in metrics),
            'total_shares': sum(item['shares'] for item in metrics),
            'avg_views': round(statistics.mean([item['views'] for item in metrics])) if metrics else 0,
            'median_views': round(statistics.median([item['views'] for item in metrics])) if metrics else 0,
            'best_views': max([item['views'] for item in metrics], default=0),
        },
        'samples': samples,
    }
    account = {
        'generated_at': raw_payload['generated_at'],
        'source_file': str(RAW_OUT),
        'source_type': 'f2_douyin_with_chrome_cookie',
        'redfox_status': sample_summary['data_source']['redfox_check'],
        'account': {**sample_summary['account_snapshot'], 'name': '唯吾', 'homepage': HOMEPAGE},
        'period': {'start': samples[-1]['published_at'] if samples else '', 'end': samples[0]['published_at'] if samples else ''},
        'works_total': len(samples),
        'june_works_total': sum(1 for item in samples if item['published_at'].startswith('2026-06')),
        'metrics': {
            'total_play_count': sample_summary['performance_summary']['total_views'],
            'june_play_count': sum(item['metrics']['views'] for item in samples if item['published_at'].startswith('2026-06')),
            'total_likes': sample_summary['performance_summary']['total_likes'],
            'june_likes': sum(item['metrics']['likes'] for item in samples if item['published_at'].startswith('2026-06')),
            'total_comments': sample_summary['performance_summary']['total_comments'],
            'total_collects': sample_summary['performance_summary']['total_saves'],
            'total_shares': sample_summary['performance_summary']['total_shares'],
            'avg_play_count': sample_summary['performance_summary']['avg_views'],
            'best_play_count': sample_summary['performance_summary']['best_views'],
            'best_like_count': max([item['likes'] for item in metrics], default=0),
        },
        'content_positioning': {
            'business_understanding': '唯吾是装修行业 IP 运营与内容获客服务，不是普通装修案例号；前端应反推账号定位与内容方向，而不是展示后端业务产值。',
            'account_position': '装企个人 IP 操盘第一视角：把装修老板、设计师、销售不会拍/不会转化的问题，拆成现场可执行的方法。',
            'north_star': '真实咨询与成交链路优先，播放和点赞用于判断内容放大价值，不作为唯一目标。',
        },
        'works': [enriched_work(item) for item in samples],
        'june_works': [enriched_work(item) for item in samples if item['published_at'].startswith('2026-06')],
        'strategy': {
            'keep': ['第一视角无脚本引导', '成品案例开篇/亮点拆解', '工地服务与开工现场的真实问题', '装企老板短视频/IP转型痛点'],
            'reduce': ['泛 AI 热点和与装企获客弱关联的人设内容', '只讲后端服务产值而不展示账号内容表现'],
            'next_actions': ['每周用 F2 链路更新账号快照和作品列表', '补咨询数/私信数作为转化字段', '下一条新稿先做发布前 blind prediction，再 T+3 回填'],
        },
    }
    SAMPLES_OUT.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    SAMPLES_OUT.write_text(json.dumps(sample_summary, ensure_ascii=False, indent=2), encoding='utf-8')
    ACCOUNT_OUT.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding='utf-8')
    return {
        'captured_works': len(samples),
        'account_aweme_count': aweme_count,
        'missing_works_count': sample_summary['account_snapshot']['missing_works_count'],
        'follower_count': profile.get('follower_count'),
        'total_favorited': profile.get('total_favorited'),
        'total_views': sample_summary['performance_summary']['total_views'],
        'outputs': [str(ACCOUNT_OUT), str(SAMPLES_OUT), str(RAW_OUT)],
    }


async def fetch(args: argparse.Namespace) -> dict[str, Any]:
    cookie_header = get_chrome_cookie_header()
    kwargs = build_f2_kwargs(cookie_header)
    kwargs['timeout'] = args.timeout
    kwargs['max_retries'] = args.max_retries
    kwargs['max_connections'] = 1
    account = Account(
        name='唯吾',
        tier='单账号分析',
        owner='W哥',
        store='唯吾装企IP',
        url=HOMEPAGE,
        expected_nickname='唯吾—装企陪跑个人运营账号',
        expected_id='27312081576',
        sec_uid=SEC_UID,
    )
    cache = load_cache(CACHE)
    result = await collect_account(
        account=account,
        kwargs=kwargs,
        cache=cache,
        start=parse_day(args.start),
        end=parse_day(args.end),
        page_count=args.page_count,
        max_pages=args.max_pages,
    )
    save_cache(CACHE, cache)
    payload = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'source': 'f2_douyin_with_chrome_cookie',
        'request': vars(args),
        'result': result_to_dict(result),
    }
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    if result.status != 'ok':
        return {'status': result.status, 'error': result.error, 'raw_output': str(RAW_OUT)}
    return {'status': 'ok', **write_outputs(payload)}


def main() -> None:
    args = parse_args()
    summary = asyncio.run(fetch(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get('status') != 'ok':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
