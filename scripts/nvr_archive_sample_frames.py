#!/usr/bin/env python3
"""Read-only sample frames from a Hikvision HTTP archive download."""
from __future__ import annotations
import argparse, csv, importlib.util, sys, uuid, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request
import av

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('gate_nvr_service',ROOT/'connector'/'gate_nvr_service.py')
assert SPEC and SPEC.loader
MODULE=importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name]=MODULE; SPEC.loader.exec_module(MODULE)
LOCAL=timezone(timedelta(hours=8))

def iso(v): return v.astimezone(LOCAL).strftime('%Y-%m-%dT%H:%M:%SZ')

def search(nvr, track, start, end):
    body=f'''<CMSearchDescription><searchID>{uuid.uuid4()}</searchID><trackList><trackID>{track}</trackID></trackList><timeSpanList><timeSpan><startTime>{iso(start)}</startTime><endTime>{iso(end)}</endTime></timeSpan></timeSpanList><maxResults>40</maxResults><searchResultPostion>0</searchResultPostion><metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList></CMSearchDescription>'''.encode()
    root=ET.fromstring(nvr.request('/ISAPI/ContentMgmt/search',body))
    out=[]
    for item in root.findall('.//{*}searchMatchItem'):
        uri=MODULE._xml_text(item,'playbackURI')
        span=item.find('.//{*}timeSpan')
        if uri and span is not None: out.append((MODULE._xml_text(span,'startTime'),MODULE._xml_text(span,'endTime'),uri))
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--recorder',default='nvr-main-02'); ap.add_argument('--track',type=int,default=1001); ap.add_argument('--start',required=True); ap.add_argument('--end',required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--step',type=float,default=1.0); args=ap.parse_args()
    creds,_=MODULE.load_credentials(False,'dws'); cred=creds[args.recorder]; nvr=MODULE.HikvisionNvr(args.recorder,cred,timeout=30)
    start=datetime.fromisoformat(args.start).replace(tzinfo=LOCAL); end=datetime.fromisoformat(args.end).replace(tzinfo=LOCAL)
    items=search(nvr,args.track,start,end)
    if not items: raise SystemExit('recording missing')
    item=items[0]
    # Search results can point at a much larger continuous recording segment.
    # Restrict the download URI to the requested wall-clock interval so frame
    # offsets and the on-screen clock line up with --start/--end.
    parts=urlsplit(item[2]); query=dict(parse_qsl(parts.query))
    query['starttime']=iso(start).replace('-','').replace(':','')
    query['endtime']=iso(end).replace('-','').replace(':','')
    playback_uri=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    body=f'<downloadRequest><playbackURI>{playback_uri.replace("&","&amp;")}</playbackURI></downloadRequest>'.encode()
    req=Request(f'http://{cred.host}/ISAPI/ContentMgmt/download',data=body,method='POST',headers={'Content-Type':'application/xml'})
    resp=nvr.opener.open(req,timeout=60); container=av.open(resp,format='mpeg'); args.out.mkdir(parents=True,exist_ok=True)
    # The HTTP download endpoint may ignore the narrowed query and still start
    # at the beginning of the continuous recording segment.  Use the segment's
    # wall-clock start to discard pre-roll and save only the requested interval.
    segment_start=datetime.strptime(item[0],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=LOCAL)
    first=None; next_t=0.0; rows=[]
    try:
        for frame in container.decode(video=0):
            t=float(frame.time or 0.0)
            if first is None: first=t
            rel=t-first
            when=segment_start+timedelta(seconds=rel)
            if when+timedelta(seconds=0.01) >= start+timedelta(seconds=next_t):
                path=args.out/f't{next_t:06.1f}.jpg'; frame.to_image().save(path,format='JPEG',quality=86,optimize=True)
                rows.append((round(next_t,1),str(path))); next_t += args.step
            if when >= end+timedelta(seconds=2): break
    finally:
        container.close(); resp.close()
    with (args.out/'frames.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['offset_seconds','image']); w.writerows(rows)
    print({'frames':len(rows),'out':str(args.out),'segments':len(items)})

if __name__=='__main__': main()
