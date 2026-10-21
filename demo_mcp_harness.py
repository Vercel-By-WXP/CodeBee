#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP Harness 演示脚本

展示如何通过 MCP 协议与编排平台交互。

使用方法：
1. 确保服务运行在 http://localhost:8765
2. 生成令牌：python demo_mcp_harness.py --create-token
3. 运行演示：python demo_mcp_harness.py --token <your-token>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 中文输出兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 添加 app 目录到路径
sys.path.insert(0, str(Path(__file__).parent / "app"))

from core import mcp_harness


def create_token():
    """生成新的 MCP 访问令牌。"""
    token = mcp_harness.create_token(user_id="demo-user")
    print("=" * 60)
    print("🔑 新生成的令牌（只展示一次）：")
    print("=" * 60)
    print(token)
    print("=" * 60)
    print("\n⚠️  请立即保存此令牌，后续无法再次查看。")
    print("\n📝 配置示例（Claude Desktop / Cursor）：")
    print(json.dumps({
        "mcpServers": {
            "orchestration": {
                "type": "http",
                "url": "http://localhost:8765/mcp/orchestration",
                "headers": {
                    "Authorization": f"Bearer {token}"
                }
            }
        }
    }, indent=2, ensure_ascii=False))
    return token


def list_tools():
    """列出所有可用工具。"""
    tools = mcp_harness.list_tools()
    print("\n📋 可用工具清单：")
    print("=" * 60)
    for tool in tools:
        print(f"\n• {tool['name']}")
        print(f"  {tool['description'].split(chr(10))[0]}")
    print(f"\n总计：{len(tools)} 个工具")


def demo_catalog():
    """演示元工具：获取工具目录。"""
    print("\n🔍 演示：orchestration_catalog")
    print("=" * 60)
    result = mcp_harness.call_tool("orchestration_catalog", {})
    print(json.dumps(result, indent=2, ensure_ascii=False))


def demo_agent_list():
    """演示：列出所有 Agent。"""
    print("\n🤖 演示：agent_list")
    print("=" * 60)
    result = mcp_harness.call_tool("agent_list", {})
    if "error" in result:
        print(f"❌ 错误：{result['error']}")
    else:
        agents = result.get("agents", [])
        print(f"找到 {len(agents)} 个 Agent：")
        for agent in agents[:5]:  # 只显示前 5 个
            print(f"  • {agent.get('id')}: {agent.get('name')}")
            print(f"    能力：{', '.join(agent.get('capabilities', []))}")


def demo_workflow_list():
    """演示：列出所有 Workflow。"""
    print("\n🔄 演示：workflow_list")
    print("=" * 60)
    result = mcp_harness.call_tool("workflow_list", {})
    if "error" in result:
        print(f"❌ 错误：{result['error']}")
    else:
        workflows = result.get("workflows", [])
        print(f"找到 {len(workflows)} 个 Workflow：")
        for wf in workflows[:5]:
            print(f"  • {wf.get('id')}: {wf.get('name')}")
            print(f"    步骤数：{wf.get('steps')}")


def demo_task_list():
    """演示：列出任务。"""
    print("\n📝 演示：task_list")
    print("=" * 60)
    result = mcp_harness.call_tool("task_list", {"limit": 10})
    if "error" in result:
        print(f"❌ 错误：{result['error']}")
    else:
        tasks = result.get("tasks", [])
        print(f"最近 {len(tasks)} 个任务：")
        for task in tasks:
            print(f"  • [{task.get('status')}] {task.get('id')}: {task.get('title')}")


def main():
    parser = argparse.ArgumentParser(description="MCP Harness 演示")
    parser.add_argument("--create-token", action="store_true", help="生成新令牌")
    parser.add_argument("--token", type=str, help="使用指定令牌")
    parser.add_argument("--list-tools", action="store_true", help="列出所有工具")
    parser.add_argument("--demo", action="store_true", help="运行完整演示")
    
    args = parser.parse_args()
    
    if args.create_token:
        create_token()
        return
    
    if args.list_tools:
        list_tools()
        return
    
    if args.demo or args.token:
        print("🚀 MCP Harness 演示")
        print("=" * 60)
        
        # 运行演示
        demo_catalog()
        demo_agent_list()
        demo_workflow_list()
        demo_task_list()
        
        print("\n" + "=" * 60)
        print("✅ 演示完成")
        print("\n💡 提示：")
        print("  • 写操作默认禁用（安全模式）")
        print("  • 启用方法：设置 mcp_harness_allow_mutating=true")
        print("  • 详见文档：docs/mcp-harness.md")
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()
