import json, urllib.request

cid = '6bfcc071-7cd7-4b01-be38-869a737e867c'
with urllib.request.urlopen('http://127.0.0.1:8000/api/review/draft?conversation_id=' + cid, timeout=15) as r:
    d = json.loads(r.read().decode('utf-8'))
secs = d.get('sections') or []
print('总字数:', d.get('total_words'))
print('章节数:', len(secs))
proc = ['核验', '卷期', '页码', '我先', '让我', '查看', '检查', '穿梭', '工具', '开始', '接下来', '现在']
for s in secs:
    c = s.get('content') or ''
    nlines = len(c.split('\n')) if c else 0
    bad = [kw for kw in proc if kw in c]
    flag = ('WARN ' + ','.join(bad)) if bad else 'OK'
    print('\n[%s | %s | %d字 | %d行] %s' % (s.get('key'), s.get('title'), len(c), nlines, flag))
    print('   片段:', repr(c[:90]))