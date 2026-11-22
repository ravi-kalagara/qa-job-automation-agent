"""Job scan engine for the QA Job Agent dashboard.

Fetches live postings from public, no-auth job APIs (Remote OK, Arbeitnow,
Jobicy), filters to QA/SDET titles, scores them against the stored resume
with JobMatcher, and records new matches in the application tracker with
status 'matched'.

It NEVER submits an application anywhere — recording a match is the end of
the pipeline. Applying stays a manual, per-role decision for the user.
"""
import html
import re
from datetime import datetime

import requests

from agent.job_matcher import JobMatcher

TITLE_RE = re.compile(
    r'\b(qa|sdet|quality assurance|quality engineer|test automation|'
    r'automation test|software test|test engineer|testing engineer)\b',
    re.IGNORECASE,
)

SOURCES = ('remoteok', 'arbeitnow', 'jobicy')
TIMEOUT = 20


def _clean(text, limit=4000):
    text = html.unescape(text or '')
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text)[:limit]


def _fetch_remoteok():
    resp = requests.get('https://remoteok.com/api',
                        headers={'User-Agent': 'qa-job-agent/1.0'}, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for item in resp.json()[1:]:  # first element is API metadata
        jobs.append({
            'id': f"remoteok-{item.get('id')}",
            'title': item.get('position', ''),
            'company': item.get('company', ''),
            'platform': 'Remote OK',
            'link': item.get('url', ''),
            'description': _clean(item.get('description', '')),
        })
    return jobs


def _fetch_arbeitnow():
    resp = requests.get('https://www.arbeitnow.com/api/job-board-api',
                        headers={'User-Agent': 'qa-job-agent/1.0'}, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for item in resp.json().get('data', []):
        jobs.append({
            'id': f"arbeitnow-{item.get('slug')}",
            'title': item.get('title', ''),
            'company': item.get('company_name', ''),
            'platform': 'Arbeitnow',
            'link': item.get('url', ''),
            'description': _clean(item.get('description', '')),
        })
    return jobs


def _fetch_jobicy():
    resp = requests.get('https://jobicy.com/api/v2/remote-jobs?count=50',
                        headers={'User-Agent': 'qa-job-agent/1.0'}, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for item in resp.json().get('jobs', []):
        jobs.append({
            'id': f"jobicy-{item.get('id')}",
            'title': item.get('jobTitle', ''),
            'company': item.get('companyName', ''),
            'platform': 'Jobicy',
            'link': item.get('url', ''),
            'description': _clean(item.get('jobExcerpt', '')),
        })
    return jobs


FETCHERS = {'remoteok': _fetch_remoteok, 'arbeitnow': _fetch_arbeitnow, 'jobicy': _fetch_jobicy}


def run_scan(tracker, resume, min_score=0.5, should_continue=None):
    """Run one scan. Returns a summary dict.

    should_continue: optional callable checked between sources; if it
    returns False the scan stops early (agent paused/stopped).
    """
    matcher = JobMatcher()
    summary = {
        'started_at': datetime.now().isoformat(),
        'finished_at': None,
        'scanned': 0,
        'qa_filtered': 0,
        'new_matches': 0,
        'duplicates': 0,
        'below_threshold': 0,
        'stopped_early': False,
        'sources': {},
    }
    for name in SOURCES:
        if should_continue is not None and not should_continue():
            summary['stopped_early'] = True
            break
        try:
            jobs = FETCHERS[name]()
            summary['sources'][name] = {'fetched': len(jobs)}
        except Exception as e:
            summary['sources'][name] = {'error': str(e)[:200]}
            continue
        summary['scanned'] += len(jobs)
        for job in jobs:
            if not TITLE_RE.search(job['title'] or ''):
                continue
            summary['qa_filtered'] += 1
            scored = matcher.match_jobs(resume, [dict(job)])
            score = scored[0]['match_score'] if scored else _raw_score(matcher, resume, job)
            if score < min_score:
                summary['below_threshold'] += 1
                continue
            job['match_score'] = score
            if job['link'] and tracker.has_applied(job['link']):
                summary['duplicates'] += 1
                continue
            if tracker.record_application(job, status='matched'):
                summary['new_matches'] += 1
    summary['finished_at'] = datetime.now().isoformat()
    return summary


def _raw_score(matcher, resume, job):
    """Score a single job even when it falls under JobMatcher's own
    built-in threshold (match_jobs drops those, we still want the number)."""
    skill = matcher._calculate_skill_match(resume.get('skills', []), job)
    exp = matcher._calculate_experience_match(resume.get('experience_years', 0), job)
    title = matcher._calculate_title_match(resume.get('job_titles', []), job)
    return (skill * 0.5) + (exp * 0.3) + (title * 0.2)
