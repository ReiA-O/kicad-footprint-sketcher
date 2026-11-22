"""Capture only windows owned by this process; never capture screen regions."""
from pathlib import Path
import ctypes
from ctypes import wintypes as w
import json, os, sys, traceback
import tkinter as tk
from PIL import Image

repo=Path(r'D:\CodexWork\projects\kicad-footprint-sketcher')
stage=Path(r'D:\CodexWork\tasks\kicad-stage1-validation-20261123')
sys.path.insert(0,str(repo))
from footprint_sketcher.gui import SketcherApp
from footprint_sketcher.persistence import load_project

def capture(window, path):
    window.update_idletasks()
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.GetClientRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.GetDC.argtypes=[w.HWND];user.GetDC.restype=w.HDC
    user.ReleaseDC.argtypes=[w.HWND,w.HDC]
    user.PrintWindow.argtypes=[w.HWND,w.HDC,w.UINT];user.PrintWindow.restype=w.BOOL
    gdi.CreateCompatibleDC.argtypes=[w.HDC];gdi.CreateCompatibleDC.restype=w.HDC
    gdi.CreateCompatibleBitmap.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];gdi.CreateCompatibleBitmap.restype=w.HANDLE
    gdi.SelectObject.argtypes=[w.HDC,w.HANDLE];gdi.SelectObject.restype=w.HANDLE
    gdi.DeleteObject.argtypes=[w.HANDLE];gdi.DeleteDC.argtypes=[w.HDC]
    gdi.GetDIBits.argtypes=[w.HDC,w.HANDLE,w.UINT,w.UINT,ctypes.c_void_p,ctypes.c_void_p,w.UINT]
    hwnd=user.GetAncestor(window.winfo_id(),2)
    pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if pid.value != os.getpid():raise RuntimeError('Capture target is not owned by this process')
    rect=w.RECT();user.GetClientRect(hwnd,ctypes.byref(rect))
    width,height=rect.right-rect.left,rect.bottom-rect.top
    dc=user.GetDC(hwnd);memory=gdi.CreateCompatibleDC(dc);bitmap=gdi.CreateCompatibleBitmap(dc,width,height)
    previous=gdi.SelectObject(memory,bitmap)
    try:
        if not user.PrintWindow(hwnd,memory,3):raise RuntimeError('PrintWindow did not render the owned window')
        class Header(ctypes.Structure):
            _fields_=[('size',w.DWORD),('width',w.LONG),('height',w.LONG),('planes',w.WORD),('bits',w.WORD),('compression',w.DWORD),('image_size',w.DWORD),('xppm',w.LONG),('yppm',w.LONG),('colors',w.DWORD),('important',w.DWORD)]
        header=Header(40,width,-height,1,32,0,width*height*4,0,0,0,0)
        data=ctypes.create_string_buffer(width*height*4)
        if gdi.GetDIBits(memory,bitmap,0,height,data,ctypes.byref(header),0) != height:raise RuntimeError('GetDIBits failed')
        image=Image.frombytes('RGB',(width,height),data.raw,'raw','BGRX')
        if all(low==high for low,high in image.getextrema()):raise RuntimeError('Owned window capture is blank')
        image.save(path)
        return dict(method='Win32 PrintWindow, client only, ownership PID verified',pid=pid.value,size=[width,height],file=path.name)
    finally:
        gdi.SelectObject(memory,previous);gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,dc)

root=tk.Tk();app=SketcherApp(root);root.geometry('1600x1000+20+20')
root.title('KiCad Footprint Sketcher - 要件補完版・撮影用インスタンス')
name,sketch,image=load_project(stage/'Requirements_Demo.kfsproj')
app.tools._loaded(name,sketch,image);app.selected_index=0;app._fill_pad_fields(sketch.pads[0]);app._refresh_pad_list();app._refresh()
report={'status':'started','method':'Only current process windows via PrintWindow','captures':[]}
def main_ready():
    try:
        report['captures'].append(capture(root,stage/'requirements-demo-main.png'))
        app.tools.open_panel(0);root.after(300,panel_ready)
    except BaseException:finish_error()
def panel_ready():
    try:
        report['captures'].append(capture(app.tools.window,stage/'requirements-demo-properties.png'))
        report['status']='completed';finish()
    except BaseException:finish_error()
def finish_error():
    report.update(status='failed',exception=traceback.format_exc());finish()
def finish():
    (stage/'expanded-ui-capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    root.destroy()
root.after(700,main_ready);root.mainloop()
