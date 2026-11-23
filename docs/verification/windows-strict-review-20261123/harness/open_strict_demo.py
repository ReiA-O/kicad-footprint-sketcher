from pathlib import Path
import json, subprocess, sys, traceback, os
import tkinter as tk

repo=Path(r'D:\CodexWork\projects\kicad-footprint-sketcher')
stage=Path(r'D:\CodexWork\tasks\kicad-stage1-validation-20261123')
sys.path.insert(0,str(repo))
from footprint_sketcher.gui import SketcherApp
from footprint_sketcher.persistence import load_project
root=tk.Tk();app=SketcherApp(root)
root.geometry('1600x1000+20+20')
root.title('KiCad Footprint Sketcher - 自己レビュー修正版・操作デモ')
name,sketch,image=load_project(stage/'Requirements_Demo.kfsproj')
app.tools._loaded(name,sketch,image)
app.selected_index=0;app._fill_pad_fields(sketch.pads[0]);app._refresh_pad_list();app._refresh()
app._message('パッド1と2は点対称・等寸法です。1の移動・幅変更で2が追従します。上部メニューから図形・穴・開口・拘束を試せます。')
def ready():
    report={'pid':os.getpid(),'commit':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True,creationflags=subprocess.CREATE_NO_WINDOW).strip(),
            'title':root.title(),'mapped':bool(root.winfo_ismapped()),'canvas':[app.canvas.winfo_width(),app.canvas.winfo_height()],
            'logical_entities':len(sketch.pads),'constraints':len(sketch.constraints),'project':str(stage/'Requirements_Demo.kfsproj')}
    (stage/'strict-gui-ready.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
root.after(350,ready)
root.mainloop()
