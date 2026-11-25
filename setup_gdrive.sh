#!/usr/bin/env bash
set -euo pipefail

XDL_HOME="${XDL_HOME:-/opt/X_Download/data}"
CONF="${XDL_HOME}/rclone.conf"
REMOTE_NAME="${1:-gdrive}"

mkdir -p "${XDL_HOME}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "错误: 未检测到 rclone，请先安装: apt-get install -y rclone"
  exit 1
fi

echo "============================================================"
echo "  XDL Google Drive 自动同步向导 (配置至 ${CONF})"
echo "============================================================"
echo "提示："
echo "1. 建议将远端名称保持为: ${REMOTE_NAME}"
echo "2. 云存储类型输入 drive (Google Drive)"
echo "3. client_id / client_secret 留空直接回车"
echo "4. scope 选择 1 (Full access)"
echo "5. root_folder_id / service_account_file / edit advanced config 均留空或选 n"
echo "6. 'Use web browser to automatically authenticate rclone with remote?' 请选 n (无头服务器)"
echo "7. 在您本机电脑(已安装 rclone)终端运行: rclone authorize \"drive\""
echo "   在本地浏览器登录您的 Google 账号并允许权限后，把终端输出的那段 JSON token 粘贴回来即可！"
echo "------------------------------------------------------------"

rclone config --config "${CONF}"

if [ -f "${CONF}" ]; then
  chmod 600 "${CONF}"
fi

echo ""
echo "正在检测已配置的远端..."
if rclone listremotes --config "${CONF}" | grep -q "^${REMOTE_NAME}:$"; then
  echo "检测连通性 (${REMOTE_NAME}:)..."
  if rclone about "${REMOTE_NAME}:" --config "${CONF}"; then
    echo "✅ Google Drive (${REMOTE_NAME}:) 授权成功！现在可在网页右侧设置中打开“自动同步到云盘”开关。"
  else
    echo "⚠️  远端已写入配置，但 about 检测失败，请检查授权是否完整。"
  fi
else
  echo "⚠️  未检测到名为 '${REMOTE_NAME}' 的远端，现有远端列表："
  rclone listremotes --config "${CONF}" || true
  echo "如果在网页设置的“目标云盘目录”中使用了其他名称，请填入 <远端名>:X_Download"
fi
