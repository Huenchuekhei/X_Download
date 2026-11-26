# iOS 快捷指令（免 Mac）配置指南

通过 XDL 的 iOS 快捷指令接入，在 iPhone 的 X App 中点击 **分享 → XDL 下载**，即可直接弹出 **视频下载时长限制框** 并派发到远程服务器异步后台下载（默认同时下载 **图片 + 视频**，并遵循服务器已配置的 X Cookie 与 Google Drive 自动同步规则）。

> 无需 Mac、无需 `.shortcut` 签名工具；所有链接展开、引用转发探测、时长预设解析全部在服务器端完成，手机端只需 8–9 个原生动作（约 2 分钟完成）。

---

## 交互逻辑说明

| 分享内容类型 | 手机端弹窗流程 | 服务器行为 |
| :--- | :--- | :--- |
| **博主主页 / 普通推文 / 纯转发** | 跳过范围菜单 → 直接弹出**视频时长限制框** | 默认下载图片 + 该博主全部媒体（更新全局时长设置后立即加入队列并启动） |
| **引用转发（Quote Tweet）** | 先弹出二选一菜单：<br>1. `只下载这个链接的`<br>2. `下载 @原作者 全部媒体`（优先原视频作者）<br>→ 再弹出**视频时长限制框** | 选择单条时以独立「单条推文」任务入队；选择博主则下载其全部媒体。均立即异步启动并返回成功通知 |

### 视频时长限制选项（全局生效）

- `不限时长`
- `≤ 1 分钟`
- `≤ 3 分钟`
- `≤ 5 分钟`
- `≤ 10 分钟`
- `自定义时长`（弹窗手填，支持 `90` 秒、`01:30` 或 `2m` / `1m30s`）

选定后会自动将网页端全局筛选更新为 `mediaType = all`（默认下载图片 + 视频）及对应的视频时长上限（或取消时长上限），其余服务器参数保持默认不变。

---

## 第一步：在 XDL 网页端获取凭据

1. 登录你的 XDL Web 控制台，在右侧边栏（手机端点击底部 **设置**）找到 **`iOS 快捷指令`** 卡片。
2. 复制卡片中的三项信息：
   - **Shortcut API Token**（填在 HTTP 请求标头 `X-XDL-Token`）
   - **识别接口 URL**：`https://你的域名/api/shortcut/inspect`
   - **提交接口 URL**：`https://你的域名/api/shortcut/submit`

---

## 第二步：在 iPhone 快捷指令 App 组装（约 2 分钟）

打开 iPhone 自带 **快捷指令** App，点击右上角 **`+`** 新建快捷指令，命名为 **`XDL 下载`**：

1. **配置共享表单触发器**：
   - 点击顶部信息栏 → 开启 **在共享表单中显示**
   - 接收类型：**取消全部，只保留 `URL` 和 `文本`**
   - 当没有输入时：点击 **继续** → 改为 **询问每次** 或 **从剪贴板获取**（推荐 **从剪贴板获取**，这样复制链接后直接点桌面快捷指令也能用）
2. **保存 API Token**：
   - 添加动作 **文本** → 粘贴你的 `Shortcut API Token`（若服务器处于无密码本地模式可跳过此步与请求标头）
3. **调用识别接口（Inspect）**：
   - 添加动作 **获取 URL 的内容**
   - URL 填识别接口：`https://你的域名/api/shortcut/inspect`
   - 展开选项：
     - 方法：**`POST`**
     - 标头：Key = `X-XDL-Token`，Value = 上一步的 **文本**
     - 请求体：**JSON** → 添加字段：`url`（文本）= **快捷指令的输入**
4. **判断是否为引用转发（Quote Tweet）**：
   - 添加动作 **如果**
   - 条件：点选上一步请求结果的 `词典` → 获取键 `isQuote` → 条件设为 **`是`** → 值填 **`yes`**
   - 在 **如果** 分支内添加 **从列表中选择**：来源选上面同一份请求结果，获取键 **`scopeOptions`**（若不需要自定义变量名，也可直接在结果上引用“选定的项目”）
   - 建议在选完后用 **设定变量** 命名为 **`scope`**；在 **否则** 分支放一个空 **文本** 也设定变量 **`scope`**
5. **弹出视频时长限制选项框**：
   - 添加动作 **从列表中选择**
   - 列表项来源：点选 Inspect 结果的键 **`durationOptions`**
   - 添加 **设定变量** 将结果设为 **`dur`**
6. **处理“自定义时长”输入**：
   - 添加动作 **如果**：`dur` **是** **`自定义时长`**
   - 分支内添加 **要求输入**（类型选 **文本**，提示填 `输入最长视频时长，如 90 或 02:30`）→ 设定变量 **`customDur`**
   - **否则** 分支内放一个空 **文本** → 设定变量 **`customDur`**
7. **提交到下载队列（Submit）**：
   - 添加动作 **获取 URL 的内容**
   - URL 填提交接口：`https://你的域名/api/shortcut/submit`
   - 展开选项：
     - 方法：**`POST`**
     - 标头：Key = `X-XDL-Token`，Value = 第 2 步的 Token **文本**
     - 请求体：**JSON**（4 个文本字段）：
       - `url` = **快捷指令的输入**
       - `scopeChoice` = 变量 **`scope`**
       - `durationChoice` = 变量 **`dur`**
       - `customDuration` = 变量 **`customDur`**
8. **显示结果通知**：
   - 添加动作 **显示通知**
   - 内容：点选上一步 Submit 结果，获取键 **`message`**（标题可设为 `XDL 下载`）

---

## 接口参考（服务端调试）

在 `hcss-ecs-fbe6` 上部署并更新代码后，可先检查语法与测试接口：

```bash
# 1. 验证 Python 语法
python3 -m py_compile xdl_auth.py x_downloader.py xdl_gui.py

# 2. 重启 XDL 服务（按实际 systemd 服务名）
sudo systemctl restart xdl-web

# 3. 测试 Inspect 接口（替换 DOMAIN 与 TOKEN）
curl -sS -X POST https://DOMAIN/api/shortcut/inspect \
  -H 'Content-Type: application/json' \
  -H 'X-XDL-Token: TOKEN' \
  -d '{"url":"https://x.com/NASA/status/1234567890"}'
```

返回字段说明：
- `POST /api/shortcut/inspect`：
  - 请求体：`{"url": "<分享文本或链接>"}`
  - 响应关键字段：`isQuote`（`"yes"` / `"no"`）、`scopeOptions`、`durationOptions`、`targetUser`、`quotedUser`、`summary`
- `POST /api/shortcut/submit`：
  - 请求体：`{"url": "...", "scopeChoice": "...", "durationChoice": "...", "customDuration": "..."}`
  - 响应关键字段：`ok`、`status`、`mode`（`"account"` / `"tweet"`）、`user`、`tweetId`、`filterSummary`、`title`、`message`
- Token 管理（需 Web 登录态）：
  - `GET /api/shortcut/config`：获取当前 Shortcut Token 与端点路径（首次访问自动生成 32 字节安全 Token）
  - `POST /api/shortcut/token/rotate`：立即轮换新 Token 并使旧 Token 失效
