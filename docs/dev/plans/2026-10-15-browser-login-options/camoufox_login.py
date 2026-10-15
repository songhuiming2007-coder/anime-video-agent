"""D96 人测脚本：用 Camoufox 持久配置开一个有界面的窗口，人手动登录 Google / YouTube，关窗即保存。

用法（全局装的 camoufox 0.5.6 在 Python 3.14 下）：
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -I camoufox_login.py <profile_dir>
第二次用同一 <profile_dir> 再跑一遍，看是否仍是登录状态（持久性）。
os 固定为 macos：Camoufox 默认每次启动随机指纹，指纹每次变可能让 Google 反复要求验证（待实测）。
"""
import sys
from camoufox.sync_api import Camoufox

profile = sys.argv[1]
with Camoufox(persistent_context=True, user_data_dir=profile, headless=False, os="macos") as ctx:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://accounts.google.com/signin/v2/identifier?service=youtube")
    print("在弹出的窗口里登录；登完打开 youtube.com 确认右上角是头像，然后回到这里按回车关闭。")
    input()
