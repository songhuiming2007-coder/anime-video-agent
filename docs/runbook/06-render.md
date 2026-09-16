# Runbook: 06 本地音视频渲染

读取已批准的排片计划，通过本地 ffmpeg 确定性一趟出片。

## 前置要求
- 必须存在 `04-clips.approved.json`
- `ffmpeg` 必须支持 `libass` 滤镜

## 执行命令
```bash
python -m pipeline.render data/episodes/<期号>
```

## 产物与位置
- `data/episodes/<期号>/05-final.mp4`（1080p 成片）

## 核心规程与内部护栏
1. **截取双重守卫强制执行**：
   - 切前校验：`seek + duration <= 源文件时长`，越界当场报错；
   - 切后校验：实际截取帧数与请求帧数相差不超过 1 帧。
2. **字幕贪心断行**：代码按标点自算折行并安全转义，不依赖 libass 的空格断词。
3. **BGM 响度与侧链闪避**：每首 BGM 按实测归一到 -26 LUFS，人声触发自动动态闪避，避免覆盖口播。
