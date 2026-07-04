#!/usr/bin/env python3
import shutil
import argparse
import pandas as pd
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument"--min-bars",type=int,default=200)
parser.add_argument"--delete",action="store_true")
parser.add_argument"--timeframe",default="1d")
args=parser.parse_args()
wd=Path("localdata/warehouse")
killed=0;kept=0
for d in sorted(wd.glob("symbol=*")):
 f=d/"Timeframe="+args.timeframe+"/data.parquet".replace("Timeframe=","timeframe=")
 if not f.exists():continue
 try: df=pd.read_parquet(f);n=len(df)
 except: n=0
 if n<args.min_bars:print(f"{d.name}:#{n}");killed+=1:if args.delete:shutil.rmtree(d)
 else:kept+=1
print(f"kept {kept} deleted {killed}")
