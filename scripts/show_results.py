import json
with open('E:/UIAutotest/parallel_results.json', encoding='utf-8') as f:
    d = json.load(f)
total = d['total_passed'] + d['total_failed'] + d['total_errors']
pct = d['total_passed'] / total * 100 if total else 0
print('通过:', d['total_passed'], '/', total, '(%.1f%%)' % pct)
print('失败:', d['total_failed'], ' 错误:', d['total_errors'])
print('耗时: %.0fs (%.1f min)' % (d['overall_duration_s'], d['overall_duration_s']/60))
print('session_id:', d.get('session_id', '(none)'))
