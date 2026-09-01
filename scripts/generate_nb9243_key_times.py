#!/usr/bin/env python3
import csv
from datetime import datetime, timedelta
from pathlib import Path

times = [
    "2026-07-07T19:28:00",  # 材多送料扫码
    "2026-07-11T16:15:00",  # 车间问数
    "2026-07-11T17:19:00",  # 陈盟首检
    "2026-07-11T17:29:00",  # 陈鹏首检
    "2026-07-11T19:58:27",  # 陈盟在11号机扫码开工
    "2026-07-14T16:32:00",  # 李海林/李鑫首检
    "2026-07-15T09:32:00",  # 车床工序记录更新时间
    "2026-07-15T20:57:00",  # 清洗节点创建
    "2026-07-15T21:40:00",  # 395件出库
]
cameras = [
    ("nvr-main-01", 49, "简易车床扫单"),
    ("nvr-main-01", 28, "铁皮房车床"),
    ("nvr-main-02", 46, "一楼车床靠里面"),
    ("nvr-main-02", 16, "车床打样区右"),
]
rows=[]
for raw in times:
    start=datetime.fromisoformat(raw)
    for recorder,channel,name in cameras:
        rows.append({
            "episode_id":f"nb9243-{recorder[-2:]}-{channel}-{start:%Y%m%d-%H%M}",
            "gate":name,"recorder":recorder,"channel":channel,
            "start_local":start.isoformat(timespec="seconds"),
            "end_local":(start+timedelta(minutes=1)).isoformat(timespec="seconds"),
            "trigger_count":1,"span_seconds":60,
        })
out=Path("lost-item-investigator/audit-output/2026-08-10-nb9243-2/key-times.csv")
out.parent.mkdir(parents=True,exist_ok=True)
with out.open("w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(len(rows))
