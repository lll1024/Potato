#!/usr/bin/env python3
"""离线证据探针；只读取帧和manifest，不访问GUI/网络/产品实现。"""
import argparse,csv,io,json,pathlib,re,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('frames',type=pathlib.Path)
ap.add_argument('--jobs',type=int,default=1)
ap.add_argument('--dedupe',action='store_true',help='相同actual_seconds仅识别一次')
ap.add_argument('--start',type=float,default=float('-inf'))
ap.add_argument('--end',type=float,default=float('inf'))
ap.add_argument('--marker',help='fixture输入独有标记，如 HANDOFF117 INPUT；与归档内容分类分开')
ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('/tmp/potato109-native-position-observations.json'))
a=ap.parse_args()
a.frames=a.frames.resolve()  # macOS Leptonica rejects /tmp symlink path; use /private/tmp.
manifest=json.loads((a.frames/'manifest.json').read_text())
observations=[]; start=time.monotonic()
frames=[f for f in manifest['frames'] if a.start <= f['actual_seconds'] <= a.end]
if a.dedupe:
    frames=list({f['actual_seconds']: f for f in frames}.values())
def recognize(frame):
    return subprocess.run(['tesseract',str(a.frames/frame['file']),'stdout','--psm','11','tsv'],text=True,errors='replace',capture_output=True)
pool=ThreadPoolExecutor(max_workers=a.jobs)
for frame, run in zip(frames,pool.map(recognize,frames)):
    if run.returncode: sys.exit(run.stderr)
    rows=[r for r in csv.DictReader(io.StringIO(run.stdout),delimiter='\t') if r['text'].strip()]
    # 顶栏/输入框不作为正文。原始480x1043帧由TSV尺寸按比例计算。
    page=next(csv.DictReader(io.StringIO(run.stdout),delimiter='\t'))
    height=int(page['height'])
    words=[r for r in rows if .12*height<float(r['top'])<.86*height]
    text=' '.join(r['text'] for r in words)
    compact=re.sub(r'[^A-Z0-9]','',text.upper())
    marker_hit=bool(a.marker and re.sub(r'[^A-Z0-9]','',a.marker.upper()) in compact)
    urls=sum(1 for r in words if re.match(r'https?://|www\.',r['text']))
    date=bool(re.search(r'(?<![\d/])2026(?!\d)',text))
    tail=bool('3.8' in text and re.search(r'10:00.{0,4}22:00',text))
    if a.marker:
        phase='INPUT_MARKER_VISIBLE' if marker_hit else 'NO_MARKER_OBSERVED'
        rollback=marker_hit
    else:
        # 仅针对本轮已知归档材料。不能推广为任意Markdown的UI测试。
        phase=('SOURCE_TABLE_TAIL' if urls>=2 else 'SAVED_ANSWER_TAIL' if tail else
               'EARLIER_INPUT_OR_TITLE' if date else 'UNKNOWN')
        rollback=phase=='EARLIER_INPUT_OR_TITLE'
    obs={**frame,'phase':phase,'rollback':rollback,'url_tokens':urls,'date':date,
         'terminal_tail_numbers':tail,'marker_hit':marker_hit,'ocr_text':text}
    observations.append(obs)
    print(f"{frame['actual_seconds']:.6f}s {phase}"+(' RED' if rollback else ''))
red=[o for o in observations if o['rollback']]
unknown=sum(o['phase']=='UNKNOWN' for o in observations)
result={'elapsed_seconds':round(time.monotonic()-start,3),'mode':'fixture-marker' if a.marker else 'archived-content',
        'marker':a.marker,'red_count':len(red),'unknown_count':unknown,'observations':observations}
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(f"红帧={len(red)} 未分类={unknown} 总帧={len(observations)} 耗时={result['elapsed_seconds']}s 输出={a.output}")
# 红=1；全部可解释且无红=0；归档未知=2。marker未命中只代表该采样未观察到，绝非保位证明。
sys.exit(1 if red else 2 if unknown else 0)
