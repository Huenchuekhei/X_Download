# XDL · X (Twitter) 媒体批量下载器

基于 `gallery-dl` 的 X (Twitter) 博主媒体批量下载工具，同时提供 **CMD 命令行模式** 与 **响应式 Web 工作台**，支持增量下载、失败重试、实时日志流以及按媒体类型和单条视频时长过滤。

## 目录结构

- `x_downloader.py`：核心命令行下载器与 SQLite 增量档案管理
- `xdl_gui.py`：Web 服务后端与 API（兼容 `--desktop` PyWebView 窗口）
- `xdl-gui/`：响应式前端界面（`index.html`、`styles.css`、`app.js`）
- `xdl.cmd`：Windows 快速启动脚本

## 环境依赖

```bash
pip install -r requirements.txt
```

需要 Python 3.12+ 与有效的 X/Twitter Cookie（支持 `cookies.txt` 或 `--cookies-from-browser`）。

## 快速使用

### 1. 启动 Web 工作台（默认）

```bash
python xdl_gui.py --web
# 或直接运行 xdl（已配置 PATH 时）
```

如需手机或局域网其他设备访问：

```bash
python xdl_gui.py --web --lan
```

### 2. 命令行下载

```bash
# 下载指定博主全部媒体
python x_downloader.py elonmusk

# 仅下载视频，且单条视频不超过 1 分钟（60 秒）
python x_downloader.py elonmusk --videos-only --max-duration 60
python x_downloader.py elonmusk --videos-only --max-duration 01:00

# 查看 / 重试失败队列
python x_downloader.py --status
python x_downloader.py --retry
```

## 主要特性

- **视频时长与媒体类型过滤**：支持 `全部媒体 / 仅视频 / 仅图片` 及单条视频最长时长限制（预设 `15秒` 到 `10分钟` 或自定义 `分:秒`，CLI 支持 `--max-duration`）；超长视频跳过且不写入已下载档案。
- **Web CMD 命令控制台**：网页端直接输入 `xdl` 命令执行，支持 `Ctrl+K` 聚焦与上下键命令历史。
- **实时流式日志**：按账号、日志级别、关键字筛选并支持导出 `.log`。
- **增量下载与失败队列**：基于 `~/.xdl/archive.sqlite3` 与 `~/.xdl/downloads.sqlite3` 避免重复下载，失败项按推文粒度重试。
- **响应式布局**：适配桌面端与移动端浏览器，支持网页内图片预览与视频播放。
