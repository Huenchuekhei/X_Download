# iOS 快捷指令（TVDL 一键安装版）

通过 XDL 的 iOS 快捷指令接入，在 iPhone 的 X App 中点击 **分享 → XDL 下载**，即可直接弹出 **视频下载时长限制框** 并派发到远程服务器异步后台下载（默认同时下载 **图片 + 视频**，并遵循服务器已配置的 X Cookie 与 Google Drive 自动同步规则）。

---

## 一、一键安装链接（推荐，像 TVDL 一样点开即装）

已在项目内置由 Apple 证书签名的 `.shortcut` 安装包，无需 Mac、无需手动拼装动作，个人 Token 与服务器域名在添加后本机填写、不会发送给第三方签名服务：

1. **在 iPhone Safari 打开一键安装页面**：
   `https://你的域名/shortcut`
   （或直接在网页端右侧 **iOS 快捷指令** 卡片点击 **一键获取 XDL 快捷指令** / **复制一键安装链接**）
2. 点击页面上的 **一键获取 XDL 快捷指令**，在 Safari 下载提示中选择 **下载** → 点下载栏打开 **XDL下载.shortcut** → 直接点 **添加快捷指令**。
3. **粘贴服务器地址与 Token**：
   - 在「快捷指令」App 中点击 **XDL下载** 右上角 **`…`** 进入编辑；
   - 第 1 个「文本」框：粘贴你的 **XDL 服务器地址**（含 `http://` 或 `https://`，如 `https://你的域名` 或 `http://1.2.3.4:8765`，在安装页或控制台均可一键复制）；
   - 第 2 个「文本」框：粘贴 **Shortcut API Token**（登录 XDL 网页端，在「iOS 快捷指令」卡片复制；若服务器为无密码本地模式留空即可），然后点 **完成**。
   > 注：`WFWorkflowImportQuestions` 已刻意留空，因为 iOS 18.5+ / iOS 26 的「快捷指令」在导入提问页回答完毕后点击“添加快捷指令”存在系统级卡死 Bug；改为添加后直接在顶部两个文本框配置即可稳定添加。
4. **开始使用**：
   - 在 iPhone 的 X App 打开任意推文或博主主页 → 点 **分享 → XDL 下载**；
   - 也可以复制链接后直接点桌面快捷指令图标，会自动读取剪贴板链接。

---

## 二、交互逻辑说明

| 分享内容类型 | 手机端弹窗流程 | 服务器行为 |
| :--- | :--- | :--- |
| **博主主页 / 普通推文 / 纯转发** | 跳过范围菜单 → 直接弹出**视频时长限制框** | 默认下载图片 + 该博主全部媒体（更新全局时长设置并立即入队启动，不覆盖已在排队任务原有规则） |
| **引用转发（Quote Tweet）** | 先弹出二选一菜单：<br>1. `只下载这个链接的`<br>2. `下载 @原作者 全部媒体`（优先原视频作者）<br>→ 再弹出**视频时长限制框** | 选择单条时以独立「单条推文」任务入队（失败重试也仅重跑该推文）；选择博主则下载其全部媒体。均立即异步启动并返回通知 |

### 视频时长限制选项（同步更新网页全局设置 + 绑定本次任务）

- `不限时长`
- `≤ 1 分钟`
- `≤ 3 分钟`
- `≤ 5 分钟`
- `≤ 10 分钟`
- `自定义时长`（弹窗手填，支持 `90` 秒、`01:30` 或 `2m` / `1m30s`）

---

## 三、备用：手动搭建与服务端调试

如果需要自定义修改快捷指令动作，可展开网页端 **`iOS 快捷指令`** 卡片中的 **`手动搭建步骤（备用）`** 逐项组装，或按需要重新构建安装包：

```bash
# 重新生成并签名 .shortcut（一般仅在修改脚本动作后执行）
python3 scripts/build_ios_shortcut.py

# 验证服务端 Python 语法
python3 -m py_compile scripts/build_ios_shortcut.py xdl_auth.py x_downloader.py xdl_gui.py

# 重启 XDL 服务后测试公共安装页与 API
curl -I https://DOMAIN/shortcut
curl -I https://DOMAIN/shortcut/XDL.shortcut
curl -sS -X POST https://DOMAIN/api/shortcut/inspect \
  -H 'Content-Type: application/json' \
  -H 'X-XDL-Token: TOKEN' \
  -d '{"url":"https://x.com/NASA/status/1234567890"}'
```

接口一览：
- `GET /shortcut`：免登录的一键安装落地页（不含任何密码或 Token）
- `GET /shortcut/XDL.shortcut`：直接下载 Apple 已签名的 `.shortcut` 安装包（亦可在登录后访问 `/api/shortcut/download`）
- `POST /api/shortcut/inspect` / `POST /api/shortcut/submit`：需有效登录 Cookie 或 `X-XDL-Token` / `Authorization: Bearer` 令牌
- `GET /api/shortcut/config` / `POST /api/shortcut/token/rotate`：需 Web 管理员登录态
