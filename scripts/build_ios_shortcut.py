#!/usr/bin/env python3
"""Generate and optionally Apple-sign the XDL iOS Shortcut (.shortcut)."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BASE_DIR / "xdl-gui" / "xdl-download.shortcut"
HUBSIGN_URL = "https://hubsign.routinehub.services/sign"
NS_UUID = uuid.UUID("6d6c3e2a-91d4-4f88-a731-1b2c3d4e5f01")


def _uid(name: str) -> str:
    return str(uuid.uuid5(NS_UUID, name)).upper()


def _action_output(output_uuid: str, output_name: str) -> dict:
    return {
        "OutputName": output_name,
        "OutputUUID": output_uuid,
        "Type": "ActionOutput",
    }


def _extension_input() -> dict:
    return {"Type": "ExtensionInput"}


def _variable_ref(name: str) -> dict:
    return {"Type": "Variable", "VariableName": name}


def _token_attachment(value: dict) -> dict:
    return {
        "Value": value,
        "WFSerializationType": "WFTextTokenAttachment",
    }


def _token_string_single(attachment: dict) -> dict:
    return {
        "Value": {
            "attachmentsByRange": {"{0, 1}": attachment},
            "string": "\ufffc",
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _token_string_with_suffix(attachment: dict, suffix: str) -> dict:
    return {
        "Value": {
            "attachmentsByRange": {"{0, 1}": attachment},
            "string": "\ufffc" + suffix,
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _plain_token_string(text: str) -> dict:
    return {
        "Value": {
            "attachmentsByRange": {},
            "string": text,
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _dict_item_token(key: str, attachment: dict) -> dict:
    return {
        "WFItemType": 0,
        "WFKey": _plain_token_string(key),
        "WFValue": _token_string_single(attachment),
    }


def _conditional_string_input(output_uuid: str, output_name: str) -> dict:
    return {
        "Type": "Variable",
        "Variable": {
            "Value": {
                "Aggrandizements": [
                    {
                        "CoercionItemClass": "WFStringContentItem",
                        "Type": "WFCoercionVariableAggrandizement",
                    }
                ],
                "OutputName": output_name,
                "OutputUUID": output_uuid,
                "Type": "ActionOutput",
            },
            "WFSerializationType": "WFTextTokenAttachment",
        },
    }


def build_shortcut_plist() -> dict:
    """Build the XML-serializable Apple Shortcuts workflow dictionary."""
    u_server = _uid("action-server")
    u_token = _uid("action-token")
    u_inspect = _uid("action-inspect")
    u_inspect_dict = _uid("action-inspect-dict")
    u_status = _uid("action-status")
    g_err = _uid("group-err")
    u_err_title = _uid("action-err-title")
    u_err_msg = _uid("action-err-msg")
    u_is_quote = _uid("action-is-quote")
    g_quote = _uid("group-quote")
    u_scope_opts = _uid("action-scope-opts")
    u_choose_scope = _uid("action-choose-scope")
    u_empty_scope = _uid("action-empty-scope")
    u_dur_opts = _uid("action-dur-opts")
    u_choose_dur = _uid("action-choose-dur")
    g_custom = _uid("group-custom")
    u_ask_custom = _uid("action-ask-custom")
    u_empty_custom = _uid("action-empty-custom")
    u_submit = _uid("action-submit")
    u_submit_dict = _uid("action-submit-dict")
    u_sub_title = _uid("action-sub-title")
    u_sub_msg = _uid("action-sub-msg")

    actions = [
        # 0: Server Base URL (Import Question #1)
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_server,
                "WFTextActionText": "https://",
            },
        },
        # 1: Shortcut API Token (Import Question #2)
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_token,
                "WFTextActionText": "",
            },
        },
        # 2: POST /api/shortcut/inspect
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": u_inspect,
                "ShowHeaders": True,
                "WFHTTPBodyType": "JSON",
                "WFHTTPMethod": "POST",
                "WFURL": _token_string_with_suffix(
                    _action_output(u_server, "文本"),
                    "/api/shortcut/inspect",
                ),
                "WFHTTPHeaders": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            _dict_item_token("X-XDL-Token", _action_output(u_token, "文本")),
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
                "WFJSONValues": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            _dict_item_token("url", _extension_input()),
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
            },
        },
        # 3: Parse JSON response as Dictionary
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.detect.dictionary",
            "WFWorkflowActionParameters": {
                "UUID": u_inspect_dict,
                "WFInput": _token_attachment(_action_output(u_inspect, "URL 的内容")),
            },
        },
        # 4: Read inspect status
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_status,
                "WFDictionaryKey": "status",
                "WFInput": _token_attachment(_action_output(u_inspect_dict, "词典")),
            },
        },
        # 5..9: If inspect failed ("status" == "error"), notify & stop early
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_err,
                "WFCondition": 4,
                "WFConditionalActionString": "error",
                "WFControlFlowMode": 0,
                "WFInput": _conditional_string_input(u_status, "词典值"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_err_title,
                "WFDictionaryKey": "title",
                "WFInput": _token_attachment(_action_output(u_inspect_dict, "词典")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_err_msg,
                "WFDictionaryKey": "message",
                "WFInput": _token_attachment(_action_output(u_inspect_dict, "词典")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
            "WFWorkflowActionParameters": {
                "WFNotificationActionSound": True,
                "WFNotificationActionTitle": _token_string_single(_action_output(u_err_title, "词典值")),
                "WFNotificationActionBody": _token_string_single(_action_output(u_err_msg, "词典值")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.exit",
            "WFWorkflowActionParameters": {},
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_err,
                "WFControlFlowMode": 2,
            },
        },
        # 11: Read isQuote ("yes" | "no")
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_is_quote,
                "WFDictionaryKey": "isQuote",
                "WFInput": _token_attachment(_action_output(u_inspect_dict, "词典")),
            },
        },
        # 12..19: If Quote Tweet, show 2-option scope picker; otherwise set empty scope
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_quote,
                "WFCondition": 4,
                "WFConditionalActionString": "yes",
                "WFControlFlowMode": 0,
                "WFInput": _conditional_string_input(u_is_quote, "词典值"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_scope_opts,
                "WFDictionaryKey": "scopeOptions",
                "WFInput": _token_attachment(_action_output(u_inspect_dict, "词典")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
            "WFWorkflowActionParameters": {
                "UUID": u_choose_scope,
                "WFChooseFromListActionPrompt": "引用转发 · 选择下载范围",
                "WFInput": _token_attachment(_action_output(u_scope_opts, "词典值")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
            "WFWorkflowActionParameters": {
                "WFVariableName": "scope",
                "WFInput": _token_attachment(_action_output(u_choose_scope, "所选项目")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_quote,
                "WFControlFlowMode": 1,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_empty_scope,
                "WFTextActionText": "",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
            "WFWorkflowActionParameters": {
                "WFVariableName": "scope",
                "WFInput": _token_attachment(_action_output(u_empty_scope, "文本")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_quote,
                "WFControlFlowMode": 2,
            },
        },
        # 20..21: Prompt video duration limit options (images always included)
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_dur_opts,
                "WFDictionaryKey": "durationOptions",
                "WFInput": _token_attachment(_action_output(u_inspect_dict, "词典")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.choosefromlist",
            "WFWorkflowActionParameters": {
                "UUID": u_choose_dur,
                "WFChooseFromListActionPrompt": "视频时长限制（图片默认全部下载）",
                "WFInput": _token_attachment(_action_output(u_dur_opts, "词典值")),
            },
        },
        # 22..28: If "自定义时长" chosen, ask user for custom duration text
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_custom,
                "WFCondition": 4,
                "WFConditionalActionString": "自定义时长",
                "WFControlFlowMode": 0,
                "WFInput": _conditional_string_input(u_choose_dur, "所选项目"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {
                "UUID": u_ask_custom,
                "WFAskActionPrompt": "输入最长视频时长（如 90、2m、01:30，0=不限）",
                "WFInputType": "Text",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
            "WFWorkflowActionParameters": {
                "WFVariableName": "customDur",
                "WFInput": _token_attachment(_action_output(u_ask_custom, "提供的输入")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_custom,
                "WFControlFlowMode": 1,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {
                "UUID": u_empty_custom,
                "WFTextActionText": "",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
            "WFWorkflowActionParameters": {
                "WFVariableName": "customDur",
                "WFInput": _token_attachment(_action_output(u_empty_custom, "文本")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": g_custom,
                "WFControlFlowMode": 2,
            },
        },
        # 29: POST /api/shortcut/submit
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": u_submit,
                "ShowHeaders": True,
                "WFHTTPBodyType": "JSON",
                "WFHTTPMethod": "POST",
                "WFURL": _token_string_with_suffix(
                    _action_output(u_server, "文本"),
                    "/api/shortcut/submit",
                ),
                "WFHTTPHeaders": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            _dict_item_token("X-XDL-Token", _action_output(u_token, "文本")),
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
                "WFJSONValues": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            _dict_item_token("url", _extension_input()),
                            _dict_item_token("scopeChoice", _variable_ref("scope")),
                            _dict_item_token("durationChoice", _action_output(u_choose_dur, "所选项目")),
                            _dict_item_token("customDuration", _variable_ref("customDur")),
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
            },
        },
        # 30..33: Parse submit response and show notification
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.detect.dictionary",
            "WFWorkflowActionParameters": {
                "UUID": u_submit_dict,
                "WFInput": _token_attachment(_action_output(u_submit, "URL 的内容")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_sub_title,
                "WFDictionaryKey": "title",
                "WFInput": _token_attachment(_action_output(u_submit_dict, "词典")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": u_sub_msg,
                "WFDictionaryKey": "message",
                "WFInput": _token_attachment(_action_output(u_submit_dict, "词典")),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
            "WFWorkflowActionParameters": {
                "WFNotificationActionSound": True,
                "WFNotificationActionTitle": _token_string_single(_action_output(u_sub_title, "词典值")),
                "WFNotificationActionBody": _token_string_single(_action_output(u_sub_msg, "词典值")),
            },
        },
    ]

    return {
        "WFQuickLookActionParameters": {},
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2302.0.4",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 59771,
            "WFWorkflowIconStartColor": 4274264319,
        },
        "WFWorkflowImportQuestions": [
            {
                "ActionIndex": 0,
                "Category": "Parameter",
                "DefaultValue": "https://",
                "ParameterKey": "WFTextActionText",
                "Text": "粘贴你的 XDL 服务器地址（含 http:// 或 https://，可在网页端「iOS 快捷指令」一键复制）",
            },
            {
                "ActionIndex": 1,
                "Category": "Parameter",
                "DefaultValue": "",
                "ParameterKey": "WFTextActionText",
                "Text": "粘贴 Shortcut API Token（在 XDL 网页端「iOS 快捷指令」复制；免密码模式留空）",
            },
        ],
        "WFWorkflowInputContentItemClasses": [
            "WFURLContentItem",
            "WFStringContentItem",
        ],
        "WFWorkflowMinimumClientVersion": 1113,
        "WFWorkflowMinimumClientVersionString": "1113",
        "WFWorkflowNoInputBehavior": {
            "Name": "WFWorkflowNoInputBehaviorGetClipboard",
            "Parameters": {},
        },
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [
            "ActionExtension",
            "NCWidget",
            "Watch",
        ],
    }


def _find_curl() -> str | None:
    """Locate curl binary for fallback signing requests."""
    return shutil.which("curl")


def _clean_proxy_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    return env


def _sign_via_hubsign(xml_plist: str, shortcut_name: str, proxy: str | None = None) -> bytes:
    payload = json.dumps(
        {"shortcutName": shortcut_name, "shortcut": xml_plist},
        ensure_ascii=False,
    ).encode("utf-8")

    last_err = "unknown error"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "curl/8.7.1",
    }

    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    elif any(os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")):
        handler = urllib.request.ProxyHandler()
    else:
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)

    for attempt in range(3):
        if attempt > 0:
            time.sleep(1.5)
        req = urllib.request.Request(
            HUBSIGN_URL,
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with opener.open(req, timeout=90) as resp:
                data = resp.read()
            if data[:4] == b"AEA1":
                return data
            last_err = f"urllib non-AEA1 payload ({len(data)} bytes): {data[:120]!r}"
        except Exception as exc:
            last_err = f"urllib={exc}"

    curl_bin = _find_curl()
    if curl_bin:
        fd, body_path = tempfile.mkstemp(prefix="xdl_hubsign_", suffix=".json")
        out_fd, out_path = tempfile.mkstemp(prefix="xdl_signed_", suffix=".shortcut")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.close(out_fd)
            env = _clean_proxy_env()
            attempts = [proxy] if proxy else [None]
            for px in attempts:
                for _retry in range(2):
                    cmd = [
                        curl_bin,
                        "-fsSL",
                        "-m",
                        "90",
                        "-H",
                        "Content-Type: application/json",
                        "-H",
                        "User-Agent: curl/8.7.1",
                        "--data-binary",
                        f"@{body_path}",
                        "-o",
                        out_path,
                        HUBSIGN_URL,
                    ]
                    if px:
                        cmd[1:1] = ["-x", px]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=100, env=env)
                    if proc.returncode == 0:
                        data = Path(out_path).read_bytes()
                        if data[:4] == b"AEA1":
                            return data
                        last_err = f"{last_err}; curl non-AEA1 payload ({len(data)} bytes): {data[:120]!r}"
                    else:
                        last_err = f"{last_err}; curl ({proc.returncode}): {proc.stderr.strip()}"
        finally:
            for p in (body_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    raise RuntimeError(f"HubSign failed ({last_err})")


def _verify_aea_bytes(data: bytes) -> list[bytes]:
    if data[:4] != b"AEA1" or len(data) < 16:
        raise ValueError("Invalid AEA1 header")
    auth_len = struct.unpack("<I", data[8:12])[0]
    if 12 + auth_len > len(data):
        raise ValueError("Truncated AEA1 auth block")
    auth = plistlib.loads(data[12:12 + auth_len])
    chain = auth.get("SigningCertificateChain") or []
    if not chain:
        raise ValueError("Missing SigningCertificateChain in AEA1 auth block")
    return list(chain)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build & sign the XDL iOS Shortcut.")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT), help="Output .shortcut path")
    parser.add_argument("--name", default="XDL 下载", help="Shortcut name for HubSign")
    parser.add_argument("--proxy", default="", help="Optional HTTP proxy URL for HubSign")
    parser.add_argument("--dump-xml", default="", help="Optional path to save unsigned XML plist")
    parser.add_argument("--no-sign", action="store_true", help="Write unsigned XML plist only")
    args = parser.parse_args()

    workflow = build_shortcut_plist()
    xml_bytes = plistlib.dumps(workflow, fmt=plistlib.FMT_XML, sort_keys=False)
    xml_text = xml_bytes.decode("utf-8")

    if args.dump_xml:
        dump_path = Path(args.dump_xml)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_bytes(xml_bytes)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.no_sign:
        out_path.write_bytes(xml_bytes)
        print(f"Wrote unsigned plist ({len(xml_bytes)} bytes) -> {out_path}")
        return 0

    signed = _sign_via_hubsign(xml_text, args.name, proxy=(args.proxy.strip() or None))
    chain = _verify_aea_bytes(signed)
    out_path.write_bytes(signed)
    print(f"Signed OK ({len(signed)} bytes, {len(chain)} certs in AEA1 chain) -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
